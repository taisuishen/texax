"""
扑克牌和牌组模块
"""
import random
from dataclasses import dataclass

SUITS = ["♠", "♥", "♦", "♣"]
SUIT_NAMES = {"♠": "spades", "♥": "hearts", "♦": "diamonds", "♣": "clubs"}
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
RANK_VALUES = {r: i for i, r in enumerate(RANKS, 2)}  # 2=2, ..., A=14


@dataclass(frozen=True)
class Card:
    rank: str
    suit: str

    @property
    def value(self) -> int:
        return RANK_VALUES[self.rank]

    def __str__(self) -> str:
        return f"{self.rank}{self.suit}"

    def __repr__(self) -> str:
        return self.__str__()

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "suit": self.suit,
            "suit_name": SUIT_NAMES[self.suit],
            "display": str(self),
        }

    def __lt__(self, other):
        return self.value < other.value

    def __eq__(self, other):
        if not isinstance(other, Card):
            return False
        return self.rank == other.rank and self.suit == other.suit

    def __hash__(self):
        return hash((self.rank, self.suit))


class Deck:
    def __init__(self):
        self.cards: list[Card] = []
        self.reset()

    def reset(self):
        self.cards = [Card(rank=r, suit=s) for s in SUITS for r in RANKS]
        self.shuffle()

    def shuffle(self):
        random.shuffle(self.cards)

    def deal(self, count: int = 1) -> list[Card]:
        if count > len(self.cards):
            raise ValueError("Not enough cards in deck")
        dealt = self.cards[:count]
        self.cards = self.cards[count:]
        return dealt

    def deal_one(self) -> Card:
        return self.deal(1)[0]

    def remaining(self) -> int:
        return len(self.cards)
