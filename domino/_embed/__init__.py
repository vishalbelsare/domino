import os
from typing import Callable, Union

import meerkat as mk
import torch

from domino._embed.encoder import Encoder

from ..registry import Registry
from .bit import bit
from .clip import clip
from .robust import robust
from .transformers import transformers

__all__ = ["clip", "bit"]

encoders = Registry(name="encoders")

encoders.register(clip, aliases=[])
encoders.register(bit, aliases=[])
encoders.register(robust, aliases=[])
encoders.register(transformers, aliases=[])


def infer_modality(col: mk.Column):

    if isinstance(col, mk.ImageColumn):
        return "image"
    elif isinstance(col, mk.ScalarColumn):
        return "text"
    else:
        raise ValueError(f"Cannot infer modality from colummn of type {type(col)}.")


def embed(
    data: mk.DataFrame,
    input_col: str,
    encoder: Union[str, Encoder] = "clip",
    modality: str = None,
    out_col: str = None,
    device: Union[int, str] = "cpu",
    mmap_dir: str = None,
    num_workers: int = 4,
    batch_size: int = 128,
    **kwargs,
) -> mk.DataFrame:
    """Embed a column of data with an encoder from the encoder registry.

    Examples
    --------
    Suppose you have an Image dataset (e.g. Imagenette, CIFAR-10) loaded into a
    `Meerkat DataFrame <https://github.com/robustness-gym/meerkat>`_. You can embed the
    images in the dataset with CLIP using a code snippet like:

    .. code-block:: python

        import meerkat as mk
        from domino import embed

        dp = mk.datasets.get("imagenette")

        dp = embed(
            data=dp,
            input_col="img",
            encoder="clip"
        )


    Args:
        data (mk.DataFrame): A DataFrame containing the data to embed.
        input_col (str): The name of the column to embed.
        encoder (Union[str, Encoder], optional): Name of the encoder to use. List
            supported encoders with ``domino.encoders``. Defaults to "clip".
            Alternatively, pass an :class:`~domino._embed.encoder.Encoder` object
            containing a custom encoder.
        modality (str, optional): The modality of the data to be embedded. Defaults to
            None, in which case the modality is inferred from the type of the input
            column.
        out_col (str, optional): The name of the column where the embeddings are stored.
            Defaults to None, in which case it is ``"{encoder}({input_col})"``.
        device (Union[int, str], optional): The device on which. Defaults to "cpu".
        mmap_dir (str, optional): The path to directory where a memory-mapped file
            containing the embeddings will be written. Defaults to None, in which case
            the embeddings are not memmapped.
        num_workers (int, optional): Number of worker processes used to load the data
            from disk. Defaults to 4.
        batch_size (int, optional): Size of the batches to  used . Defaults to 128.
        **kwargs: Additional keyword arguments are passed to the encoder. To see
            supported arguments for each encoder, see the encoder documentation (e.g.
            :func:`~domino._embed.clip`).

    Returns:
        mk.DataFrame: A view of ``data`` with a new column containing the embeddings.
        This column will be named according to the ``out_col`` parameter.
    """
    if modality is None:

        modality = infer_modality(col=data[input_col])

    if out_col is None:
        out_col = f"{encoder}({input_col})"

    encoder = encoders.get(encoder, device=device, **kwargs)

    if modality not in encoder:
        raise ValueError(f'Encoder "{encoder}" does not support modality "{modality}".')

    encoder = encoder[modality]

    return _embed(
        data=data,
        input_col=input_col,
        out_col=out_col,
        encode=encoder.encode,
        preprocess=encoder.preprocess,
        collate=encoder.collate,
        device=device,
        mmap_dir=mmap_dir,
        num_workers=num_workers,
        batch_size=batch_size,
    )


def _embed(
    data: mk.DataFrame,
    input_col: str,
    out_col: str,
    encode: Callable,
    preprocess: Callable,
    collate: Callable,
    device: int = None,
    mmap_dir: str = None,
    num_workers: int = 4,
    batch_size: int = 128,
):
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    if preprocess is not None:
        embed_input = data[input_col].defer(preprocess)
    else:
        embed_input = data[input_col]

    if collate is not None:
        embed_input.collate_fn = collate

    def _prepare_input(x):
        if isinstance(x, mk.Column):
            x = x.data
        if torch.is_tensor(x):
            x = x.to(device)
        return x

    with torch.no_grad():
        data[out_col] = embed_input.map(
            lambda x: encode(_prepare_input(x)).cpu().detach().numpy(),
            pbar=True,
            is_batched_fn=True,
            batch_size=batch_size,
            num_workers=num_workers,
            mmap=mmap_dir is not None,
            mmap_path=None
            if mmap_dir is None
            else os.path.join(mmap_dir, "emb_mmap.npy"),
            flush_size=128,
        )
    return data
