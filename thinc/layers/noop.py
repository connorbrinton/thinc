from typing import Tuple, Callable, TypeVar

from ..model import Model


InOutT = TypeVar("InOutT")


def noop(*layers: Model) -> Model[InOutT, InOutT]:
    """Transform a sequences of layers into a null operation."""
    return Model("noop", forward, layers=layers)


def forward(
    model: Model[InOutT, InOutT], X: InOutT, is_train: bool
) -> Tuple[InOutT, Callable]:
    def backprop(dY: InOutT) -> InOutT:
        return dY

    return X, backprop
