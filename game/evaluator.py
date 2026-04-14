"""
德州扑克完整牌力评估器

支持从最多7张牌中选出最佳5张牌组合
手牌等级 (从高到低):
  10 - 皇家同花顺 Royal Flush
   9 - 同花顺 Straight Flush
   8 - 四条 Four of a Kind
   7 - 葫芦 Full House
   6 - 同花 Flush
   5 - 顺子 Straight
   4 - 三条 Three of a Kind
   3 - 两对 Two Pair
   2 - 一对 One Pair
   1 - 高牌 High Card
"""
from itertools import combinations
from game.deck import Card

HAND_RANK_NAMES = {
    10: "皇家同花顺",
    9: "同花顺",
    8: "四条",
    7: "葫芦",
    6: "同花",
    5: "顺子",
    4: "三条",
    3: "两对",
    2: "一对",
    1: "高牌",
}

HAND_RANK_NAMES_EN = {
    10: "Royal Flush",
    9: "Straight Flush",
    8: "Four of a Kind",
    7: "Full House",
    6: "Flush",
    5: "Straight",
    4: "Three of a Kind",
    3: "Two Pair",
    2: "One Pair",
    1: "High Card",
}


class HandResult:
    """手牌评估结果，支持比较"""

    def __init__(self, rank: int, score: tuple, best_five: list[Card], name: str):
        self.rank = rank
        self.score = score  # (rank, *kickers) 用于精确比较
        self.best_five = best_five
        self.name = name

    def __lt__(self, other):
        return self.score < other.score

    def __gt__(self, other):
        return self.score > other.score

    def __eq__(self, other):
        return self.score == other.score

    def __le__(self, other):
        return self.score <= other.score

    def __ge__(self, other):
        return self.score >= other.score

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "name": self.name,
            "name_en": HAND_RANK_NAMES_EN.get(self.rank, ""),
            "best_five": [c.to_dict() for c in self.best_five],
            "score": list(self.score),
        }

    def __repr__(self):
        cards_str = " ".join(str(c) for c in self.best_five)
        return f"{self.name} [{cards_str}]"


def _evaluate_five(cards: list[Card]) -> HandResult:
    """评估恰好5张牌的牌力"""
    assert len(cards) == 5

    values = sorted([c.value for c in cards], reverse=True)
    suits = [c.suit for c in cards]
    sorted_cards = sorted(cards, key=lambda c: c.value, reverse=True)

    is_flush = len(set(suits)) == 1

    # 检查顺子 (含 A-2-3-4-5 小顺子)
    is_straight = False
    straight_high = 0
    unique_vals = sorted(set(values), reverse=True)

    if len(unique_vals) == 5:
        if unique_vals[0] - unique_vals[4] == 4:
            is_straight = True
            straight_high = unique_vals[0]
        elif unique_vals == [14, 5, 4, 3, 2]:
            is_straight = True
            straight_high = 5  # A做1的小顺子，最高牌是5

    # 统计每个点数的数量
    from collections import Counter
    value_counts = Counter(values)
    counts = sorted(value_counts.values(), reverse=True)
    # 按 (数量降序, 点数降序) 排列
    count_groups = sorted(value_counts.items(), key=lambda x: (x[1], x[0]), reverse=True)

    # 皇家同花顺
    if is_flush and is_straight and straight_high == 14:
        score = (10,)
        return HandResult(10, score, sorted_cards, HAND_RANK_NAMES[10])

    # 同花顺
    if is_flush and is_straight:
        score = (9, straight_high)
        if straight_high == 5:
            order = sorted(cards, key=lambda c: (c.value if c.value != 14 else 1), reverse=True)
        else:
            order = sorted_cards
        return HandResult(9, score, order, HAND_RANK_NAMES[9])

    # 四条
    if counts == [4, 1]:
        quad_val = count_groups[0][0]
        kicker = count_groups[1][0]
        score = (8, quad_val, kicker)
        ordered = sorted(cards, key=lambda c: (0 if c.value == quad_val else 1, -c.value))
        return HandResult(8, score, ordered, HAND_RANK_NAMES[8])

    # 葫芦
    if counts == [3, 2]:
        trip_val = count_groups[0][0]
        pair_val = count_groups[1][0]
        score = (7, trip_val, pair_val)
        ordered = sorted(cards, key=lambda c: (0 if c.value == trip_val else 1, -c.value))
        return HandResult(7, score, ordered, HAND_RANK_NAMES[7])

    # 同花
    if is_flush:
        score = (6,) + tuple(values)
        return HandResult(6, score, sorted_cards, HAND_RANK_NAMES[6])

    # 顺子
    if is_straight:
        score = (5, straight_high)
        if straight_high == 5:
            order = sorted(cards, key=lambda c: (c.value if c.value != 14 else 1), reverse=True)
        else:
            order = sorted_cards
        return HandResult(5, score, order, HAND_RANK_NAMES[5])

    # 三条
    if counts == [3, 1, 1]:
        trip_val = count_groups[0][0]
        kickers = sorted([v for v, c in count_groups if c == 1], reverse=True)
        score = (4, trip_val) + tuple(kickers)
        ordered = sorted(cards, key=lambda c: (0 if c.value == trip_val else 1, -c.value))
        return HandResult(4, score, ordered, HAND_RANK_NAMES[4])

    # 两对
    if counts == [2, 2, 1]:
        pairs = sorted([v for v, c in count_groups if c == 2], reverse=True)
        kicker = [v for v, c in count_groups if c == 1][0]
        score = (3, pairs[0], pairs[1], kicker)
        ordered = sorted(cards, key=lambda c: (
            0 if c.value == pairs[0] else (1 if c.value == pairs[1] else 2),
            -c.value
        ))
        return HandResult(3, score, ordered, HAND_RANK_NAMES[3])

    # 一对
    if counts == [2, 1, 1, 1]:
        pair_val = count_groups[0][0]
        kickers = sorted([v for v, c in count_groups if c == 1], reverse=True)
        score = (2, pair_val) + tuple(kickers)
        ordered = sorted(cards, key=lambda c: (0 if c.value == pair_val else 1, -c.value))
        return HandResult(2, score, ordered, HAND_RANK_NAMES[2])

    # 高牌
    score = (1,) + tuple(values)
    return HandResult(1, score, sorted_cards, HAND_RANK_NAMES[1])


def evaluate(cards: list[Card]) -> HandResult:
    """
    评估最佳手牌。
    接受2-7张牌，自动从中选出最佳5张牌组合。
    """
    if len(cards) < 5:
        # 不足5张时直接评估 (用于展示, 不常见)
        padded = cards + [Card("2", "♠")] * (5 - len(cards))
        return _evaluate_five(padded)

    if len(cards) == 5:
        return _evaluate_five(cards)

    # 从所有可能的5张牌组合中找出最佳的
    best = None
    for combo in combinations(cards, 5):
        result = _evaluate_five(list(combo))
        if best is None or result > best:
            best = result
    return best


def compare_hands(hands: list[tuple[str, list[Card]]]) -> list[tuple[str, HandResult, int]]:
    """
    比较多个玩家的手牌，返回排名结果。

    参数: [(player_id, cards), ...]
    返回: [(player_id, result, ranking), ...] ranking从1开始, 1为最好
    """
    results = []
    for player_id, cards in hands:
        result = evaluate(cards)
        results.append((player_id, result))

    # 按牌力降序排列
    results.sort(key=lambda x: x[1].score, reverse=True)

    # 分配排名 (相同牌力并列)
    ranked = []
    current_rank = 1
    for i, (pid, res) in enumerate(results):
        if i > 0 and res.score < results[i - 1][1].score:
            current_rank = i + 1
        ranked.append((pid, res, current_rank))

    return ranked
