from typing import Tuple, Callable

from ..types import Array, Ragged
from ..model import Model


# TODO: more specific types?
InT = Ragged
OutT = Array


def MaxPool() -> Model[InT, OutT]:
    return Model("max_pool", forward)


def forward(model: Model[InT, OutT], Xr: InT, is_train: bool) -> Tuple[OutT, Callable]:
    Y, which = model.ops.max_pool(Xr.data, Xr.lengths)
    lengths = Xr.lengths

    def backprop(dY: OutT) -> InT:
        return Ragged(model.ops.backprop_max_pool(dY, which, lengths), lengths)

    return Y, backprop
