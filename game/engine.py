"""
德州扑克游戏引擎
管理一桌游戏的完整生命周期
"""
import asyncio
import time
import logging
from enum import Enum
from dataclasses import dataclass, field
from game.deck import Deck, Card
from game.evaluator import evaluate, HandResult, HAND_RANK_NAMES

logger = logging.getLogger("poker.engine")


class GamePhase(str, Enum):
    WAITING = "waiting"
    PRE_FLOP = "pre_flop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


class PlayerStatus(str, Enum):
    SITTING = "sitting"        # 坐下但没参与当前手牌
    ACTIVE = "active"          # 参与当前手牌
    FOLDED = "folded"          # 已弃牌
    ALL_IN = "all_in"          # 全押


@dataclass
class Player:
    user_id: str
    username: str
    seat: int
    chips: int = 0
    hole_cards: list[Card] = field(default_factory=list)
    status: PlayerStatus = PlayerStatus.SITTING
    current_bet: int = 0       # 当前下注轮的下注额
    total_bet: int = 0         # 本手牌总下注
    is_ready: bool = False
    last_action: str = ""
    last_action_amount: int = 0

    def reset_for_hand(self):
        self.hole_cards = []
        self.status = PlayerStatus.ACTIVE
        self.current_bet = 0
        self.total_bet = 0
        self.last_action = ""
        self.last_action_amount = 0

    def to_dict(self, show_cards: bool = False) -> dict:
        data = {
            "user_id": self.user_id,
            "username": self.username,
            "seat": self.seat,
            "chips": self.chips,
            "status": self.status.value,
            "current_bet": self.current_bet,
            "total_bet": self.total_bet,
            "is_ready": self.is_ready,
            "last_action": self.last_action,
            "last_action_amount": self.last_action_amount,
        }
        if show_cards:
            data["hole_cards"] = [c.to_dict() for c in self.hole_cards]
        else:
            data["hole_cards_count"] = len(self.hole_cards)
        return data


@dataclass
class PotInfo:
    amount: int
    eligible_players: list[str]  # user_ids


class GameEngine:
    """单桌德州扑克游戏引擎"""

    def __init__(self, broadcast_callback=None):
        self.players: dict[str, Player] = {}  # user_id -> Player
        self.seats: dict[int, str] = {}       # seat_number -> user_id

        self.deck = Deck()
        self.community_cards: list[Card] = []
        self.phase = GamePhase.WAITING
        self.pots: list[PotInfo] = []
        self.main_pot = 0

        self.dealer_seat = -1
        self.small_blind_seat = -1
        self.big_blind_seat = -1
        self.current_player_seat = -1

        self.small_blind = 10
        self.big_blind = 20
        self.turn_timeout = 30
        self.max_players = 9

        self.current_bet = 0  # 当前轮最高下注
        self.min_raise = 0    # 最小加注额

        self._turn_timer_task: asyncio.Task | None = None
        self._broadcast = broadcast_callback

        self.hand_number = 0
        self.last_hand_results: list | None = None

    def update_config(self, small_blind: int, big_blind: int, turn_timeout: int, max_players: int):
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.turn_timeout = turn_timeout
        self.max_players = max_players

    # ─── 座位管理 ───

    def sit_down(self, user_id: str, username: str, chips: int, seat: int) -> bool:
        if seat in self.seats:
            return False
        if seat < 0 or seat >= self.max_players:
            return False
        if user_id in self.players:
            return False

        player = Player(user_id=user_id, username=username, seat=seat, chips=chips)
        self.players[user_id] = player
        self.seats[seat] = user_id
        return True

    def stand_up(self, user_id: str) -> bool:
        if user_id not in self.players:
            return False
        if self.phase != GamePhase.WAITING:
            player = self.players[user_id]
            if player.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN):
                return False  # 游戏进行中不能离开
        player = self.players[user_id]
        del self.seats[player.seat]
        del self.players[user_id]
        return True

    def get_player(self, user_id: str) -> Player | None:
        return self.players.get(user_id)

    def _get_active_seats(self) -> list[int]:
        """获取所有参与当前手牌的座位号 (按座位号排序)"""
        seats = []
        for uid, p in self.players.items():
            if p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN):
                seats.append(p.seat)
        return sorted(seats)

    def _get_acting_seats(self) -> list[int]:
        """获取所有还能行动的座位号 (未弃牌且未全押)"""
        seats = []
        for uid, p in self.players.items():
            if p.status == PlayerStatus.ACTIVE:
                seats.append(p.seat)
        return sorted(seats)

    def _next_seat(self, current_seat: int, seat_list: list[int]) -> int:
        """在给定座位列表中找到下一个座位"""
        if not seat_list:
            return -1
        for s in seat_list:
            if s > current_seat:
                return s
        return seat_list[0]

    # ─── 游戏流程 ───

    async def try_start_game(self) -> bool:
        """尝试开始新一局"""
        if self.phase != GamePhase.WAITING:
            return False

        ready_players = [p for p in self.players.values() if p.is_ready and p.chips > 0]
        if len(ready_players) < 2:
            return False

        await self._start_hand(ready_players)
        return True

    async def _start_hand(self, participants: list[Player]):
        """开始一手牌"""
        self.hand_number += 1
        self.deck.reset()
        self.community_cards = []
        self.pots = []
        self.main_pot = 0
        self.last_hand_results = None

        for p in participants:
            p.reset_for_hand()

        # 移动庄家按钮
        active_seats = sorted([p.seat for p in participants])
        if self.dealer_seat == -1:
            self.dealer_seat = active_seats[0]
        else:
            self.dealer_seat = self._next_seat(self.dealer_seat, active_seats)

        # 确定大小盲位
        if len(active_seats) == 2:
            # 两人时，庄家=小盲
            self.small_blind_seat = self.dealer_seat
            self.big_blind_seat = self._next_seat(self.dealer_seat, active_seats)
        else:
            self.small_blind_seat = self._next_seat(self.dealer_seat, active_seats)
            self.big_blind_seat = self._next_seat(self.small_blind_seat, active_seats)

        # 下盲注
        sb_player = self.players[self.seats[self.small_blind_seat]]
        bb_player = self.players[self.seats[self.big_blind_seat]]
        self._place_bet(sb_player, min(self.small_blind, sb_player.chips))
        self._place_bet(bb_player, min(self.big_blind, bb_player.chips))

        # 发手牌
        for p in participants:
            p.hole_cards = self.deck.deal(2)

        # 设置翻前状态
        self.phase = GamePhase.PRE_FLOP
        self.current_bet = self.big_blind
        self.min_raise = self.big_blind

        # 翻前从大盲的下一位开始
        self.current_player_seat = self._next_seat(self.big_blind_seat, self._get_acting_seats())

        await self._broadcast_state("hand_start")
        await self._start_turn_timer()

    def _place_bet(self, player: Player, amount: int):
        """玩家下注"""
        actual = min(amount, player.chips)
        player.chips -= actual
        player.current_bet += actual
        player.total_bet += actual
        self.main_pot += actual
        if player.chips == 0:
            player.status = PlayerStatus.ALL_IN
        return actual

    async def player_action(self, user_id: str, action: str, amount: int = 0) -> dict:
        """处理玩家行动"""
        player = self.players.get(user_id)
        if not player:
            return {"ok": False, "error": "你不在桌上"}
        if self.phase == GamePhase.WAITING or self.phase == GamePhase.SHOWDOWN:
            return {"ok": False, "error": "当前不是行动阶段"}
        if player.seat != self.current_player_seat:
            return {"ok": False, "error": "还没轮到你"}
        if player.status != PlayerStatus.ACTIVE:
            return {"ok": False, "error": "你无法行动"}

        self._cancel_turn_timer()

        call_amount = self.current_bet - player.current_bet

        if action == "fold":
            player.status = PlayerStatus.FOLDED
            player.last_action = "弃牌"
            player.last_action_amount = 0

        elif action == "check":
            if call_amount > 0:
                return {"ok": False, "error": "需要跟注，不能过牌"}
            player.last_action = "过牌"
            player.last_action_amount = 0

        elif action == "call":
            if call_amount <= 0:
                return {"ok": False, "error": "没有需要跟的注"}
            actual = self._place_bet(player, call_amount)
            player.last_action = "跟注"
            player.last_action_amount = actual

        elif action == "raise":
            if amount < self.current_bet + self.min_raise and amount < player.chips + player.current_bet:
                return {"ok": False, "error": f"加注至少为 {self.current_bet + self.min_raise}"}
            raise_to = amount
            bet_needed = raise_to - player.current_bet
            if bet_needed >= player.chips:
                # 全押
                actual = self._place_bet(player, player.chips)
                new_total = player.current_bet
            else:
                actual = self._place_bet(player, bet_needed)
                new_total = player.current_bet

            if new_total > self.current_bet:
                self.min_raise = new_total - self.current_bet
                self.current_bet = new_total

            player.last_action = "加注" if player.status != PlayerStatus.ALL_IN else "全押"
            player.last_action_amount = actual

        elif action == "allin":
            actual = self._place_bet(player, player.chips)
            if player.current_bet > self.current_bet:
                self.min_raise = max(self.min_raise, player.current_bet - self.current_bet)
                self.current_bet = player.current_bet
            player.last_action = "全押"
            player.last_action_amount = actual

        else:
            return {"ok": False, "error": f"未知行动: {action}"}

        await self._broadcast_state("player_action")

        # 检查是否需要进入下一阶段
        await self._advance_game()

        return {"ok": True, "action": action}

    async def _advance_game(self):
        """推进游戏状态"""
        active_seats = self._get_active_seats()
        acting_seats = self._get_acting_seats()

        # 只剩一人，直接获胜
        if len(active_seats) == 1:
            winner_uid = self.seats[active_seats[0]]
            winner = self.players[winner_uid]
            winner.chips += self.main_pot
            self.last_hand_results = [{
                "user_id": winner_uid,
                "username": winner.username,
                "won": self.main_pot,
                "hand": None,
                "reason": "其他玩家弃牌",
            }]
            self.phase = GamePhase.SHOWDOWN
            await self._broadcast_state("hand_end")
            await asyncio.sleep(3)
            await self._reset_for_next_hand()
            return

        # 检查当前下注轮是否结束
        if self._is_betting_round_complete():
            await self._next_phase()
        else:
            # 移到下一位玩家
            self.current_player_seat = self._next_seat(self.current_player_seat, acting_seats)
            await self._start_turn_timer()

    def _is_betting_round_complete(self) -> bool:
        """检查当前下注轮是否结束"""
        acting_seats = self._get_acting_seats()
        if not acting_seats:
            return True  # 所有人都all-in或弃牌

        for seat in acting_seats:
            uid = self.seats[seat]
            p = self.players[uid]
            if p.current_bet < self.current_bet:
                return False
            if not p.last_action and self.phase != GamePhase.PRE_FLOP:
                return False
            # 翻前: 大盲有权再行动
            if self.phase == GamePhase.PRE_FLOP:
                if seat == self.big_blind_seat and not p.last_action:
                    return False

        # 确认下一个该行动的人不是还没行动过的
        next_seat = self._next_seat(self.current_player_seat, acting_seats)
        if next_seat != -1:
            next_uid = self.seats[next_seat]
            next_p = self.players[next_uid]
            if next_p.current_bet < self.current_bet:
                return False
            if not next_p.last_action:
                if self.phase != GamePhase.PRE_FLOP:
                    return False
                if next_seat == self.big_blind_seat:
                    return False

        return True

    async def _next_phase(self):
        """进入下一阶段"""
        # 重置下注状态
        for p in self.players.values():
            if p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN):
                p.current_bet = 0
                p.last_action = ""
        self.current_bet = 0

        acting_seats = self._get_acting_seats()

        if self.phase == GamePhase.PRE_FLOP:
            self.phase = GamePhase.FLOP
            self.community_cards.extend(self.deck.deal(3))
        elif self.phase == GamePhase.FLOP:
            self.phase = GamePhase.TURN
            self.community_cards.extend(self.deck.deal(1))
        elif self.phase == GamePhase.TURN:
            self.phase = GamePhase.RIVER
            self.community_cards.extend(self.deck.deal(1))
        elif self.phase == GamePhase.RIVER:
            await self._showdown()
            return

        await self._broadcast_state("new_phase")

        # 如果没有可以行动的玩家了 (全都all-in), 直接发完公共牌
        if len(acting_seats) <= 1:
            await asyncio.sleep(1)
            await self._next_phase()
            return

        # 从庄家下一位开始
        active_seats = self._get_active_seats()
        self.current_player_seat = self._next_seat(self.dealer_seat, acting_seats)
        await self._start_turn_timer()

    async def _showdown(self):
        """摊牌阶段"""
        self.phase = GamePhase.SHOWDOWN
        active_players = [p for p in self.players.values()
                          if p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN)]

        # 计算边池
        pots = self._calculate_pots()

        self.last_hand_results = []
        total_awarded = {}

        for pot_amount, eligible_uids in pots:
            # 评估这个池子里每个人的牌力
            hands = []
            for uid in eligible_uids:
                p = self.players[uid]
                all_cards = p.hole_cards + self.community_cards
                result = evaluate(all_cards)
                hands.append((uid, result))

            # 找到最好的手牌
            hands.sort(key=lambda x: x[1].score, reverse=True)
            best_score = hands[0][1].score

            # 可能有并列赢家
            winners = [(uid, res) for uid, res in hands if res.score == best_score]
            share = pot_amount // len(winners)
            remainder = pot_amount % len(winners)

            for i, (uid, res) in enumerate(winners):
                won = share + (1 if i < remainder else 0)
                total_awarded[uid] = total_awarded.get(uid, 0) + won

        # 发放奖池
        for uid, won in total_awarded.items():
            self.players[uid].chips += won

        # 构建结果
        for p in active_players:
            all_cards = p.hole_cards + self.community_cards
            result = evaluate(all_cards)
            won = total_awarded.get(p.user_id, 0)
            self.last_hand_results.append({
                "user_id": p.user_id,
                "username": p.username,
                "hole_cards": [c.to_dict() for c in p.hole_cards],
                "best_hand": result.to_dict(),
                "won": won,
            })

        await self._broadcast_state("showdown")
        await asyncio.sleep(5)
        await self._reset_for_next_hand()

    def _calculate_pots(self) -> list[tuple[int, list[str]]]:
        """计算主池和边池"""
        # 收集所有参与者的总下注
        active_bets = []
        for p in self.players.values():
            if p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN, PlayerStatus.FOLDED):
                if p.total_bet > 0:
                    active_bets.append((p.user_id, p.total_bet, p.status))

        if not active_bets:
            return []

        # 找到所有不同的下注等级 (all-in的金额)
        all_in_amounts = sorted(set(
            bet for uid, bet, status in active_bets
            if status == PlayerStatus.ALL_IN
        ))

        eligible = [uid for uid, bet, status in active_bets
                     if status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN)]

        if not all_in_amounts:
            # 没有人all-in, 一个主池
            return [(self.main_pot, eligible)]

        pots = []
        prev_level = 0

        for level in all_in_amounts:
            pot_amount = 0
            pot_eligible = []
            for uid, bet, status in active_bets:
                contribution = min(bet, level) - min(bet, prev_level)
                pot_amount += contribution
                if bet >= level and status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN):
                    pot_eligible.append(uid)
            if pot_amount > 0 and pot_eligible:
                pots.append((pot_amount, pot_eligible))
            prev_level = level

        # 剩余的归主池
        remaining = 0
        remaining_eligible = []
        for uid, bet, status in active_bets:
            contribution = bet - min(bet, prev_level)
            remaining += contribution
            if contribution > 0 and status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN) and bet > prev_level:
                remaining_eligible.append(uid)

        if remaining > 0 and remaining_eligible:
            pots.append((remaining, remaining_eligible))

        # 如果没有边池，返回简单的主池
        if not pots:
            return [(self.main_pot, eligible)]

        return pots

    async def _reset_for_next_hand(self):
        """重置准备下一手牌"""
        self._cancel_turn_timer()
        self.phase = GamePhase.WAITING
        self.community_cards = []
        self.current_bet = 0
        self.current_player_seat = -1

        # 踢出没有筹码的玩家
        broke_players = [uid for uid, p in self.players.items() if p.chips <= 0]
        for uid in broke_players:
            p = self.players[uid]
            p.status = PlayerStatus.SITTING
            p.is_ready = False

        for p in self.players.values():
            p.hole_cards = []
            p.current_bet = 0
            p.total_bet = 0
            p.last_action = ""
            if p.chips > 0:
                p.status = PlayerStatus.SITTING

        await self._broadcast_state("round_end")

    # ─── 计时器 ───

    async def _start_turn_timer(self):
        self._cancel_turn_timer()
        self._turn_timer_task = asyncio.create_task(self._turn_timeout_handler())

    def _cancel_turn_timer(self):
        if self._turn_timer_task and not self._turn_timer_task.done():
            self._turn_timer_task.cancel()
            self._turn_timer_task = None

    async def _turn_timeout_handler(self):
        """超时自动弃牌/过牌"""
        try:
            await asyncio.sleep(self.turn_timeout)
            if self.current_player_seat == -1:
                return
            uid = self.seats.get(self.current_player_seat)
            if not uid:
                return
            player = self.players.get(uid)
            if not player or player.status != PlayerStatus.ACTIVE:
                return

            # 能过牌就过牌，否则弃牌
            call_amount = self.current_bet - player.current_bet
            if call_amount <= 0:
                await self.player_action(uid, "check")
            else:
                await self.player_action(uid, "fold")
        except asyncio.CancelledError:
            pass

    # ─── 状态获取 ───

    def get_state(self, for_user_id: str | None = None) -> dict:
        """获取当前游戏状态 (针对特定玩家的视角)"""
        players_data = []
        for uid, p in self.players.items():
            show_cards = False
            if for_user_id == uid:
                show_cards = True
            elif self.phase == GamePhase.SHOWDOWN and p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN):
                show_cards = True
            players_data.append(p.to_dict(show_cards=show_cards))

        # 排序
        players_data.sort(key=lambda x: x["seat"])

        # 可行动选项
        actions = []
        if for_user_id and self.phase not in (GamePhase.WAITING, GamePhase.SHOWDOWN):
            player = self.players.get(for_user_id)
            if player and player.seat == self.current_player_seat and player.status == PlayerStatus.ACTIVE:
                call_amount = self.current_bet - player.current_bet
                if call_amount <= 0:
                    actions.append({"action": "check", "label": "过牌"})
                else:
                    actions.append({"action": "call", "label": f"跟注 {call_amount}", "amount": call_amount})
                actions.append({"action": "fold", "label": "弃牌"})
                min_raise_to = self.current_bet + self.min_raise
                if player.chips + player.current_bet > self.current_bet:
                    actions.append({
                        "action": "raise",
                        "label": "加注",
                        "min": min_raise_to,
                        "max": player.chips + player.current_bet,
                    })
                actions.append({
                    "action": "allin",
                    "label": f"全押 {player.chips}",
                    "amount": player.chips,
                })

        return {
            "phase": self.phase.value,
            "hand_number": self.hand_number,
            "players": players_data,
            "community_cards": [c.to_dict() for c in self.community_cards],
            "main_pot": self.main_pot,
            "current_bet": self.current_bet,
            "dealer_seat": self.dealer_seat,
            "small_blind_seat": self.small_blind_seat,
            "big_blind_seat": self.big_blind_seat,
            "current_player_seat": self.current_player_seat,
            "small_blind": self.small_blind,
            "big_blind": self.big_blind,
            "turn_timeout": self.turn_timeout,
            "actions": actions,
            "last_hand_results": self.last_hand_results,
            "seats_count": self.max_players,
        }

    async def _broadcast_state(self, event: str = "update"):
        """广播游戏状态"""
        if self._broadcast:
            await self._broadcast(event, self)
