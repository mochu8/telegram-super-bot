"""入群验证题目。答案只放在数据库里，不放进按钮数据以外的可作弊位置。

选择题的选项本来就会出现在按钮上，这是给用户看的；正确项不会单独标出。
"""

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Challenge:
    prompt: str
    answer: str
    choices: tuple[str, ...]


def make_challenge(mode: str, rng: random.Random | None = None) -> Challenge:
    picker = rng or random
    if mode != "captcha":
        return Challenge(prompt="", answer="ok", choices=("ok",))
    left = picker.randint(1, 9)
    right = picker.randint(1, 9)
    answer = left + right
    options = {answer}
    while len(options) < 4:
        options.add(picker.randint(0, 18))
    choices = [str(number) for number in options]
    picker.shuffle(choices)
    return Challenge(prompt=f"{left} + {right}", answer=str(answer), choices=tuple(choices))


def callback_data(user_id: int, choice: str) -> str:
    data = f"v:{int(user_id)}:{choice}"
    if len(data.encode("utf-8")) > 64:
        raise ValueError("验证按钮数据过长")
    return data


def parse_callback_data(data: str) -> tuple[int, str] | None:
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "v":
        return None
    try:
        user_id = int(parts[1])
    except ValueError:
        return None
    if not parts[2]:
        return None
    return user_id, parts[2]
