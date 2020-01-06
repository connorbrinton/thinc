from typing import Tuple, List, Callable, cast

from ..model import Model
from ..types import Floats2d


InT = List[Floats2d]
OutT = Floats2d


def list2ragged() -> Model[InT, OutT]:
    """Transform sequences to ragged arrays if necessary. If sequences are
    already ragged, do nothing. A ragged array is a tuple (data, lengths),
    where data is the concatenated data.
    """
    return Model("list2array", forward)


def forward(model: Model[InT, OutT], Xs: InT, is_train: bool) -> Tuple[OutT, Callable]:
    lengths = model.ops.asarray([len(x) for x in Xs], dtype="i")

    def backprop(dY: OutT) -> InT:
        return cast(InT, model.ops.unflatten(dY, lengths))

    return cast(OutT, model.ops.flatten(Xs)), backprop