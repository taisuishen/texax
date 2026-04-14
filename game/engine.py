"""
德州扑克游戏引擎
管理一桌游戏的完整生命周期
"""
import asyncio
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
    SITTING = "sitting"
    ACTIVE = "active"
    FOLDED = "folded"
    ALL_IN = "all_in"


@dataclass
class Player:
    user_id: str
    username: str
    seat: int
    chips: int = 0
    hole_cards: list[Card] = field(default_factory=list)
    status: PlayerStatus = PlayerStatus.SITTING
    current_bet: int = 0
    total_bet: int = 0
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
    eligible_players: list[str]


class GameEngine:
    """单桌德州扑克游戏引擎"""

    def __init__(self, broadcast_callback=None, is_online_callback=None):
        self.players: dict[str, Player] = {}
        self.seats: dict[int, str] = {}

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
        self.max_players = 6

        self.current_bet = 0
        self.min_raise = 0

        # ★ 核心: 用集合追踪还需要行动的玩家座位号
        self._players_to_act: set[int] = set()

        self._turn_timer_task: asyncio.Task | None = None
        self._broadcast = broadcast_callback
        self._is_online = is_online_callback  # 检查玩家是否在线
        self._action_lock = asyncio.Lock()

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
                return False
        player = self.players[user_id]
        del self.seats[player.seat]
        del self.players[user_id]
        return True

    def get_player(self, user_id: str) -> Player | None:
        return self.players.get(user_id)

    def _get_active_seats(self) -> list[int]:
        """参与当前手牌的座位 (ACTIVE + ALL_IN)"""
        return sorted(s for uid, p in self.players.items()
                       for s in [p.seat] if p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN))

    def _get_acting_seats(self) -> list[int]:
        """还能行动的座位 (仅ACTIVE, 排除 ALL_IN 和 FOLDED)"""
        return sorted(p.seat for p in self.players.values() if p.status == PlayerStatus.ACTIVE)

    def _next_seat(self, current_seat: int, seat_list: list[int]) -> int:
        """在座位列表中找 current_seat 之后的下一个座位 (环形)"""
        if not seat_list:
            return -1
        for s in seat_list:
            if s > current_seat:
                return s
        return seat_list[0]

    def _next_to_act(self) -> int:
        """找到下一个需要行动的座位, 从 current_player_seat 的下一位开始找"""
        acting = self._get_acting_seats()
        # 只看还在 _players_to_act 里的
        candidates = sorted(s for s in acting if s in self._players_to_act)
        if not candidates:
            return -1
        # 从当前座位之后找
        for s in candidates:
            if s > self.current_player_seat:
                return s
        return candidates[0]

    # ─── 游戏流程 ───

    async def try_start_game(self) -> bool:
        if self.phase != GamePhase.WAITING:
            return False
        ready_players = [p for p in self.players.values() if p.is_ready and p.chips > 0]
        if len(ready_players) < 2:
            return False
        await self._start_hand(ready_players)
        return True

    async def _start_hand(self, participants: list[Player]):
        self.hand_number += 1
        self.deck.reset()
        self.community_cards = []
        self.pots = []
        self.main_pot = 0
        self.last_hand_results = None
        self._players_to_act.clear()

        for p in participants:
            p.reset_for_hand()

        active_seats = sorted([p.seat for p in participants])

        # 移动庄家按钮
        if self.dealer_seat == -1:
            self.dealer_seat = active_seats[0]
        else:
            self.dealer_seat = self._next_seat(self.dealer_seat, active_seats)

        # 确定大小盲位
        if len(active_seats) == 2:
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

        # 翻前状态
        self.phase = GamePhase.PRE_FLOP
        self.current_bet = self.big_blind
        self.min_raise = self.big_blind

        # ★ 翻前: 所有能行动的人都需要行动 (包括大盲, 大盲有option)
        acting = self._get_acting_seats()
        self._players_to_act = set(acting)

        # 翻前从大盲下一位开始
        first = self._next_seat(self.big_blind_seat, acting)
        self.current_player_seat = first

        logger.info(f"Hand #{self.hand_number} started. Dealer={self.dealer_seat} "
                    f"SB={self.small_blind_seat} BB={self.big_blind_seat} "
                    f"First={first} ToAct={self._players_to_act}")

        await self._broadcast_state("hand_start")
        await self._start_turn_timer()

    def _place_bet(self, player: Player, amount: int):
        actual = min(amount, player.chips)
        player.chips -= actual
        player.current_bet += actual
        player.total_bet += actual
        self.main_pot += actual
        if player.chips == 0:
            player.status = PlayerStatus.ALL_IN
        return actual

    async def player_action(self, user_id: str, action: str, amount: int = 0) -> dict:
        async with self._action_lock:
            return await self._do_player_action(user_id, action, amount)

    async def _do_player_action(self, user_id: str, action: str, amount: int = 0) -> dict:
        player = self.players.get(user_id)
        if not player:
            return {"ok": False, "error": "你不在桌上"}
        if self.phase in (GamePhase.WAITING, GamePhase.SHOWDOWN):
            return {"ok": False, "error": "当前不是行动阶段"}
        if player.seat != self.current_player_seat:
            return {"ok": False, "error": "还没轮到你"}
        if player.status != PlayerStatus.ACTIVE:
            return {"ok": False, "error": "你无法行动"}

        self._cancel_turn_timer()

        call_amount = self.current_bet - player.current_bet
        did_raise = False

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
            bet_needed = amount - player.current_bet
            if bet_needed >= player.chips:
                actual = self._place_bet(player, player.chips)
            else:
                actual = self._place_bet(player, bet_needed)

            if player.current_bet > self.current_bet:
                self.min_raise = player.current_bet - self.current_bet
                self.current_bet = player.current_bet
                did_raise = True

            player.last_action = "加注" if player.status != PlayerStatus.ALL_IN else "全押"
            player.last_action_amount = actual

        elif action == "allin":
            actual = self._place_bet(player, player.chips)
            if player.current_bet > self.current_bet:
                self.min_raise = max(self.min_raise, player.current_bet - self.current_bet)
                self.current_bet = player.current_bet
                did_raise = True
            player.last_action = "全押"
            player.last_action_amount = actual

        else:
            return {"ok": False, "error": f"未知行动: {action}"}

        # ★ 从待行动集合中移除当前玩家
        self._players_to_act.discard(player.seat)

        # ★ 如果加注了, 其他所有还能行动的人都需要重新行动
        if did_raise:
            acting = self._get_acting_seats()
            self._players_to_act = set(acting)
            self._players_to_act.discard(player.seat)  # 加注者自己不用再行动

        logger.info(f"Player {player.username}(seat {player.seat}) action={action} "
                    f"amount={amount} did_raise={did_raise} "
                    f"remaining_to_act={self._players_to_act}")

        # ★ 不在这里广播，让 _advance_game 在最终状态确定后统一广播
        await self._advance_game()

        return {"ok": True, "action": action}

    async def _advance_game(self):
        active_seats = self._get_active_seats()
        acting_seats = self._get_acting_seats()

        # 只剩一人 → 直接获胜
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

        # ★ 只看还在 _players_to_act 中且仍 ACTIVE 的座位
        remaining = set(s for s in self._players_to_act if s in set(acting_seats))
        self._players_to_act = remaining

        if not remaining:
            # 当前下注轮结束 → 进入下一阶段
            logger.info(f"Betting round complete in phase {self.phase.value}, advancing...")
            await self._next_phase()
        else:
            # 移到下一个需要行动的人
            next_s = self._next_to_act()
            if next_s == -1:
                # 不应该发生, 保险
                await self._next_phase()
                return
            self.current_player_seat = next_s
            logger.info(f"Next to act: seat {next_s}, remaining={remaining}")
            await self._broadcast_state("next_turn")
            await self._start_turn_timer()

    async def _next_phase(self):
        # 重置所有人的下注轮状态
        for p in self.players.values():
            if p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN):
                p.current_bet = 0
                p.last_action = ""
        self.current_bet = 0
        self.min_raise = self.big_blind
        self._players_to_act.clear()

        if self.phase == GamePhase.PRE_FLOP:
            self.phase = GamePhase.FLOP
            # ★ 翻牌: 逐张发, 每张间隔3秒
            for i in range(3):
                self.community_cards.extend(self.deck.deal(1))
                await self._broadcast_state("new_card")
                if i < 2:
                    await asyncio.sleep(3)
        elif self.phase == GamePhase.FLOP:
            self.phase = GamePhase.TURN
            await asyncio.sleep(3)
            self.community_cards.extend(self.deck.deal(1))
        elif self.phase == GamePhase.TURN:
            self.phase = GamePhase.RIVER
            await asyncio.sleep(3)
            self.community_cards.extend(self.deck.deal(1))
        elif self.phase == GamePhase.RIVER:
            await self._showdown()
            return

        acting_seats = self._get_acting_seats()

        # 所有人都all-in了 → 直接跳到下一阶段发牌
        if len(acting_seats) <= 1:
            await self._broadcast_state("new_phase")
            await asyncio.sleep(3)
            await self._next_phase()
            return

        # ★ 新一轮: 所有能行动的人都需要行动
        self._players_to_act = set(acting_seats)

        # 从庄家下一位开始
        first = self._next_seat(self.dealer_seat, acting_seats)
        self.current_player_seat = first

        logger.info(f"New phase: {self.phase.value} first_to_act={first} to_act={self._players_to_act}")

        await self._broadcast_state("new_phase")
        await self._start_turn_timer()

    async def _showdown(self):
        self.phase = GamePhase.SHOWDOWN
        self.current_player_seat = -1
        active_players = [p for p in self.players.values()
                          if p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN)]

        pots = self._calculate_pots()

        self.last_hand_results = []
        total_awarded = {}

        for pot_amount, eligible_uids in pots:
            hands = []
            for uid in eligible_uids:
                p = self.players[uid]
                all_cards = p.hole_cards + self.community_cards
                result = evaluate(all_cards)
                hands.append((uid, result))

            hands.sort(key=lambda x: x[1].score, reverse=True)
            best_score = hands[0][1].score
            winners = [(uid, res) for uid, res in hands if res.score == best_score]
            share = pot_amount // len(winners)
            remainder = pot_amount % len(winners)

            for i, (uid, res) in enumerate(winners):
                won = share + (1 if i < remainder else 0)
                total_awarded[uid] = total_awarded.get(uid, 0) + won

        for uid, won in total_awarded.items():
            self.players[uid].chips += won

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
        active_bets = []
        for p in self.players.values():
            if p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN, PlayerStatus.FOLDED):
                if p.total_bet > 0:
                    active_bets.append((p.user_id, p.total_bet, p.status))

        if not active_bets:
            return []

        all_in_amounts = sorted(set(
            bet for uid, bet, status in active_bets
            if status == PlayerStatus.ALL_IN
        ))

        eligible = [uid for uid, bet, status in active_bets
                     if status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN)]

        if not all_in_amounts:
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

        remaining = 0
        remaining_eligible = []
        for uid, bet, status in active_bets:
            contribution = bet - min(bet, prev_level)
            remaining += contribution
            if contribution > 0 and status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN) and bet > prev_level:
                remaining_eligible.append(uid)

        if remaining > 0 and remaining_eligible:
            pots.append((remaining, remaining_eligible))

        if not pots:
            return [(self.main_pot, eligible)]

        return pots

    async def _reset_for_next_hand(self):
        self._cancel_turn_timer()
        self.phase = GamePhase.WAITING
        self.community_cards = []
        self.current_bet = 0
        self.current_player_seat = -1
        self._players_to_act.clear()

        # 踢掉断线的玩家和没筹码的玩家
        to_remove = []
        for uid, p in self.players.items():
            is_online = self._is_online(uid) if self._is_online else True
            if not is_online or p.chips <= 0:
                to_remove.append(uid)

        for uid in to_remove:
            p = self.players[uid]
            seat = p.seat
            del self.players[uid]
            del self.seats[seat]
            logger.info(f"Removed player {uid} (offline or broke)")

        # 重置剩余玩家状态, 不自动准备
        for p in self.players.values():
            p.hole_cards = []
            p.current_bet = 0
            p.total_bet = 0
            p.last_action = ""
            p.status = PlayerStatus.SITTING
            p.is_ready = False  # ★ 每局结束后需要重新准备

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
            call_amount = self.current_bet - player.current_bet
            if call_amount <= 0:
                await self.player_action(uid, "check")
            else:
                await self.player_action(uid, "fold")
        except asyncio.CancelledError:
            pass

    # ─── 状态获取 ───

    def get_state(self, for_user_id: str | None = None) -> dict:
        players_data = []
        for uid, p in self.players.items():
            show_cards = False
            if for_user_id == uid:
                show_cards = True
            elif self.phase == GamePhase.SHOWDOWN and p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN):
                show_cards = True
            players_data.append(p.to_dict(show_cards=show_cards))

        players_data.sort(key=lambda x: x["seat"])

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
        if self._broadcast:
            await self._broadcast(event, self)
