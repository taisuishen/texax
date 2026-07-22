import asyncio
import unittest

from game.deck import Card
from game.engine import GameEngine, GamePhase, PlayerStatus
from avatar_config import DEFAULT_AVATAR_ID, avatar_url, is_valid_avatar


class FriendGameFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = GameEngine()
        self.engine.sit_down("a", "Alice", 1000, 0)
        self.engine.sit_down("b", "Bob", 1000, 1)

    async def asyncTearDown(self):
        tasks = [self.engine._show_choice_task, self.engine._next_hand_task,
                 self.engine._turn_timer_task, self.engine._single_player_idle_task]
        for task in tasks:
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in tasks if task), return_exceptions=True)

    async def test_fold_win_waits_for_optional_show(self):
        alice = self.engine.get_player("a")
        bob = self.engine.get_player("b")
        alice.status = PlayerStatus.ACTIVE
        alice.hole_cards = [Card("A", "♠"), Card("K", "♠")]
        alice.total_bet = 60
        bob.status = PlayerStatus.FOLDED
        bob.total_bet = 60
        self.engine.phase = GamePhase.PRE_FLOP
        self.engine.main_pot = 120

        await self.engine._advance_game()

        self.assertEqual(alice.chips, 1120)
        self.assertEqual(self.engine.pending_show_user_id, "a")
        self.assertNotIn("hole_cards", self.engine.get_state("b")["players"][0])

        result = await self.engine.choose_show_cards("a", True)
        self.assertTrue(result["ok"])
        self.assertIn("hole_cards", self.engine.get_state("b")["players"][0])

    async def test_unequal_all_in_refunds_unmatched_excess(self):
        alice = self.engine.get_player("a")
        bob = self.engine.get_player("b")
        alice.status = PlayerStatus.ALL_IN
        bob.status = PlayerStatus.ALL_IN
        alice.chips = bob.chips = 0
        alice.total_bet = 1000
        bob.total_bet = 500
        alice.hole_cards = [Card("2", "♠"), Card("3", "♣")]
        bob.hole_cards = [Card("A", "♠"), Card("A", "♥")]
        self.engine.community_cards = [Card("K", "♣"), Card("Q", "♦"), Card("9", "♠"),
                                       Card("7", "♥"), Card("4", "♣")]
        self.engine.main_pot = 1500
        self.engine.phase = GamePhase.RIVER

        await self.engine._showdown()

        self.assertEqual(alice.chips, 500)  # 未被跟注的 500 原路退回
        self.assertEqual(bob.chips, 1000)   # 只能赢取双方各 500 的有效底池
        bob_result = next(row for row in self.engine.last_hand_results if row["user_id"] == "b")
        alice_result = next(row for row in self.engine.last_hand_results if row["user_id"] == "a")
        self.assertEqual(bob_result["won"], 1000)
        self.assertEqual(bob_result["profit"], 500)
        self.assertEqual(alice_result["returned"], 500)

    async def test_three_player_side_pots_and_unmatched_excess(self):
        self.engine.sit_down("c", "Carol", 1000, 2)
        alice = self.engine.get_player("a")
        bob = self.engine.get_player("b")
        carol = self.engine.get_player("c")

        for player in (alice, bob, carol):
            player.status = PlayerStatus.ALL_IN
            player.chips = 0

        alice.total_bet = 1000
        bob.total_bet = 600
        carol.total_bet = 300
        alice.hole_cards = [Card("2", "♠"), Card("3", "♣")]
        bob.hole_cards = [Card("K", "♠"), Card("K", "♥")]
        carol.hole_cards = [Card("A", "♠"), Card("A", "♥")]
        self.engine.community_cards = [
            Card("Q", "♣"), Card("J", "♦"), Card("9", "♠"),
            Card("7", "♥"), Card("4", "♣"),
        ]
        self.engine.main_pot = 1900
        self.engine.phase = GamePhase.RIVER

        await self.engine._showdown()

        # 主池 900 归 Carol，300-600 的边池 600 归 Bob；
        # Alice 高于第二名投入的 400 无人跟注，必须原路退回。
        self.assertEqual(carol.chips, 900)
        self.assertEqual(bob.chips, 600)
        self.assertEqual(alice.chips, 400)
        results = {row["user_id"]: row for row in self.engine.last_hand_results}
        self.assertEqual(results["c"]["won"], 900)
        self.assertEqual(results["b"]["won"], 600)
        self.assertEqual(results["a"]["returned"], 400)
        self.assertEqual(sum(player.chips for player in (alice, bob, carol)), 1900)

    async def test_rebuy_updates_friend_game_ledger(self):
        alice = self.engine.get_player("a")
        alice.chips = 0
        alice.pending_rebuy = True

        result = await self.engine.rebuy("a", 1000)

        self.assertTrue(result["ok"])
        self.assertEqual(alice.chips, 1000)
        self.assertEqual(alice.rebuy_count, 1)
        self.assertEqual(alice.rebuy_total, 1000)
        self.assertEqual(self.engine.get_settlement()[0]["net"], -1000)

    async def test_zero_chip_player_can_sit_then_borrow_buyin(self):
        self.engine.stand_up("a")
        self.assertTrue(self.engine.sit_down("a", "Alice", 0, 0))
        alice = self.engine.get_player("a")
        self.assertTrue(alice.pending_rebuy)
        self.assertFalse(alice.is_ready)

        result = await self.engine.rebuy("a", 1000)

        self.assertTrue(result["ok"])
        self.assertEqual(alice.chips, 1000)
        self.assertFalse(alice.pending_rebuy)
        self.assertEqual(alice.rebuy_count, 1)

    async def test_players_stay_ready_and_can_leave_after_hand(self):
        alice = self.engine.get_player("a")
        bob = self.engine.get_player("b")
        alice.leave_after_hand = True
        alice.status = PlayerStatus.ACTIVE
        bob.status = PlayerStatus.ACTIVE

        await self.engine._enter_settling()

        self.assertIsNone(self.engine.get_player("a"))
        self.assertTrue(bob.is_ready)

    async def test_single_remaining_player_returns_to_waiting_and_can_leave(self):
        alice = self.engine.get_player("a")
        bob = self.engine.get_player("b")
        alice.leave_after_hand = True
        alice.status = PlayerStatus.ACTIVE
        bob.status = PlayerStatus.ACTIVE
        alice.hole_cards = [Card("A", "♠"), Card("K", "♠")]
        bob.hole_cards = [Card("Q", "♥"), Card("J", "♥")]
        self.engine.community_cards = [Card("2", "♣"), Card("3", "♦"), Card("4", "♠")]
        self.engine.main_pot = 200

        await self.engine._enter_settling()
        self.assertEqual(self.engine.phase, GamePhase.SETTLING)
        self.assertTrue(bob.is_ready)

        self.assertTrue(await self.engine.reset_if_insufficient_players())
        self.assertEqual(self.engine.phase, GamePhase.WAITING)
        self.assertEqual(self.engine.community_cards, [])
        self.assertEqual(self.engine.main_pot, 0)
        self.assertEqual(bob.hole_cards, [])
        self.assertFalse(bob.is_ready)
        self.assertTrue(self.engine.stand_up("b"))
        self.assertEqual(self.engine.players, {})

    async def test_single_player_idle_activity_resets_timer_then_auto_leaves(self):
        self.engine.single_player_idle_timeout = 0.05
        self.assertTrue(self.engine.stand_up("a"))

        await asyncio.sleep(0.03)
        self.engine.touch_single_player_activity("b")
        await asyncio.sleep(0.03)
        self.assertIsNotNone(self.engine.get_player("b"))

        await asyncio.sleep(0.04)
        self.assertIsNone(self.engine.get_player("b"))
        self.assertEqual(self.engine.phase, GamePhase.WAITING)
        self.assertEqual(self.engine.community_cards, [])
        self.assertEqual(self.engine.main_pot, 0)

    async def test_all_seated_players_must_be_ready_before_cards_are_dealt(self):
        alice = self.engine.get_player("a")
        bob = self.engine.get_player("b")
        alice.is_ready = True

        self.assertFalse(await self.engine.try_start_game())
        self.assertEqual(len(alice.hole_cards), 0)

        bob.is_ready = True
        self.assertTrue(await self.engine.try_start_game())
        self.assertEqual(self.engine.phase, GamePhase.PRE_FLOP)
        self.assertEqual(len(alice.hole_cards), 2)
        self.assertEqual(len(bob.hole_cards), 2)
        self.assertFalse(self.engine.stand_up("a"))

    async def test_all_remaining_all_in_players_are_revealed(self):
        alice = self.engine.get_player("a")
        bob = self.engine.get_player("b")
        alice.status = PlayerStatus.ALL_IN
        bob.status = PlayerStatus.ALL_IN
        alice.hole_cards = [Card("A", "♠"), Card("K", "♠")]
        bob.hole_cards = [Card("Q", "♥"), Card("Q", "♦")]

        self.assertTrue(self.engine._reveal_all_in_players())
        self.assertIn("hole_cards", self.engine.get_state("a")["players"][1])
        self.assertIn("hole_cards", self.engine.get_state("b")["players"][0])

    async def test_completed_action_immediately_hides_action_buttons(self):
        alice = self.engine.get_player("a")
        alice.status = PlayerStatus.ACTIVE
        self.engine.phase = GamePhase.FLOP
        self.engine.current_player_seat = alice.seat
        self.engine._players_to_act = {alice.seat}
        self.assertTrue(self.engine.get_state("a")["actions"])

        self.engine._players_to_act.discard(alice.seat)
        self.assertEqual(self.engine.get_state("a")["actions"], [])

    async def test_only_call_or_fold_when_all_opponents_are_all_in(self):
        alice = self.engine.get_player("a")
        bob = self.engine.get_player("b")
        alice.status = PlayerStatus.ACTIVE
        bob.status = PlayerStatus.ALL_IN
        alice.current_bet = 100
        bob.current_bet = 500
        self.engine.current_bet = 500
        self.engine.phase = GamePhase.FLOP
        self.engine.current_player_seat = alice.seat
        self.engine._players_to_act = {alice.seat}

        action_names = [row["action"] for row in self.engine.get_state("a")["actions"]]
        self.assertEqual(action_names, ["call", "fold"])

        result = await self.engine.player_action("a", "raise", 800)
        self.assertFalse(result["ok"])
        self.assertIn("只能跟注或弃牌", result["error"])

    async def test_raise_remains_available_with_an_active_opponent_in_multiway_pot(self):
        self.engine.sit_down("c", "Carol", 1000, 2)
        alice = self.engine.get_player("a")
        bob = self.engine.get_player("b")
        carol = self.engine.get_player("c")
        alice.status = PlayerStatus.ACTIVE
        bob.status = PlayerStatus.ALL_IN
        carol.status = PlayerStatus.ACTIVE
        self.engine.current_bet = 200
        alice.current_bet = 100
        self.engine.phase = GamePhase.FLOP
        self.engine.current_player_seat = alice.seat
        self.engine._players_to_act = {alice.seat, carol.seat}

        action_names = [row["action"] for row in self.engine.get_state("a")["actions"]]
        self.assertIn("raise", action_names)
        self.assertIn("allin", action_names)

    async def test_dealing_hides_real_cards_and_actions_until_animation_finishes(self):
        alice = self.engine.get_player("a")
        alice.status = PlayerStatus.ACTIVE
        alice.hole_cards = [Card("A", "♠"), Card("K", "♥")]
        self.engine.phase = GamePhase.PRE_FLOP
        self.engine.current_player_seat = alice.seat
        self.engine._players_to_act = {alice.seat}
        self.engine.dealing = True

        dealing_state = self.engine.get_state("a")
        alice_state = next(row for row in dealing_state["players"] if row["user_id"] == "a")
        self.assertNotIn("hole_cards", alice_state)
        self.assertEqual(alice_state["hole_cards_count"], 2)
        self.assertEqual(dealing_state["actions"], [])
        result = await self.engine.player_action("a", "fold")
        self.assertFalse(result["ok"])

        self.engine.dealing = False
        self.assertIn("hole_cards", self.engine.get_state("a")["players"][0])
        self.assertTrue(self.engine.get_state("a")["actions"])

    async def test_turn_timeout_always_folds_even_when_check_is_free(self):
        alice = self.engine.get_player("a")
        bob = self.engine.get_player("b")
        alice.status = PlayerStatus.ACTIVE
        bob.status = PlayerStatus.ACTIVE
        self.engine.phase = GamePhase.FLOP
        self.engine.current_player_seat = alice.seat
        self.engine.current_bet = 0
        self.engine._players_to_act = {alice.seat, bob.seat}
        self.engine.turn_timeout = 0
        self.engine._turn_timer_task = asyncio.current_task()

        await self.engine._turn_timeout_handler()

        self.assertEqual(alice.status, PlayerStatus.FOLDED)
        self.assertEqual(alice.last_action, "弃牌")
        self.assertTrue(alice.leave_after_hand)
        self.assertTrue(alice.timed_out)
        self.assertFalse(self.engine.request_leave_after_hand("a"))
        self.assertFalse(asyncio.current_task().cancelled())

    async def test_turn_id_and_remaining_time_support_reconnect_sync(self):
        self.engine.turn_timeout = 30

        await self.engine._start_turn_timer()
        first_turn_id = self.engine.turn_id
        first_state = self.engine.get_state("a")
        await self.engine._start_turn_timer()
        second_state = self.engine.get_state("a")

        self.assertGreater(first_turn_id, 0)
        self.assertEqual(second_state["turn_id"], first_turn_id + 1)
        self.assertGreater(first_state["turn_remaining"], 0)
        self.assertLessEqual(first_state["turn_remaining"], self.engine.turn_timeout)
        self.assertGreater(second_state["turn_remaining"], 0)
        self.assertLessEqual(second_state["turn_remaining"], self.engine.turn_timeout)

    async def test_avatar_is_whitelisted_and_included_in_player_state(self):
        self.engine.sit_down("c", "Carol", 1000, 2, "avatars16")

        self.assertTrue(is_valid_avatar("avatars16"))
        self.assertFalse(is_valid_avatar("../../secret"))
        self.assertEqual(avatar_url("../../secret"),
                         "/static/avatars/avatar-01.svg")
        self.assertEqual(DEFAULT_AVATAR_ID, "avatar-01")
        carol = next(player for player in self.engine.get_state("c")["players"]
                     if player["user_id"] == "c")
        self.assertEqual(carol["avatar_id"], "avatars16")


if __name__ == "__main__":
    unittest.main()
