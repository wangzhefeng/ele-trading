from abc import ABC, abstractmethod
from typing import Dict


class BaseModelMainClass(ABC):

    def __init__(self, args: Dict):
        self.args = args

    @abstractmethod
    def run(self, input_data, model_cfgs):
        pass
