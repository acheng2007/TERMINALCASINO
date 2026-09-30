"""Deterministic settlement, input, and session tests for both slots variants."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from casino.accounts import Account
from casino.config import Config
from casino import stats as stats_module
from casino.games.slots import play_slots, play_slots_expanded
from casino.games.slots import base, constants
from casino.games.slots.variants import expanded, standard
from casino.games.slots.views import view
from casino.types import GameContext


LOSING_ROW = ("A", "B", "C")
LOSING_GRID = (LOSING_ROW,) * 3
WINNING_GRID = (("D",) * 3, ("A",) * 3, LOSING_ROW)


@pytest.fixture
def context():
    return GameContext(Account.generate("slots test", 100), Config.default())


@pytest.fixture(autouse=True)
def terminal(monkeypatch):
    """Replace terminal I/O and skip the cosmetic spin animation."""
    terminal = SimpleNamespace(
        prompt=Mock(side_effect=AssertionError("Unexpected input request")),
        output=Mock(),
        spin=Mock(),
    )
    for module in (view, stats_module):
        monkeypatch.setattr(module, "cinput", terminal.prompt)
        monkeypatch.setattr(module, "cprint", terminal.output)
        monkeypatch.setattr(module, "clear_screen", Mock())
    monkeypatch.setattr(view, "display_topbar", Mock())
    monkeypatch.setattr(view.SlotsView, "render_row", Mock())
    monkeypatch.setattr(view.SlotsView, "render_grid", Mock())
    monkeypatch.setattr(base.Slots, "spin_animation", terminal.spin)
    return terminal


@pytest.fixture(autouse=True)
def losing_outcomes(monkeypatch):
    """Default to a loss; payout tests override the outcome they need."""
    monkeypatch.setattr(standard.random, "random", lambda: 0.9)
    monkeypatch.setattr(standard.random, "randint", lambda low, high: 0)
    monkeypatch.setattr(standard.StandardSlots, "random_result", lambda self: LOSING_ROW)
    monkeypatch.setattr(expanded, "get_rand_grid", lambda: LOSING_GRID)


@pytest.fixture(params=[standard.StandardSlots, expanded.ExpandedSlots],
                ids=["standard", "expanded"])
def game(request, context):
    return request.param(context)


def script(terminal, *answers):
    terminal.prompt.side_effect = answers
    return terminal.prompt


def bet_for(game, amount):
    return amount if isinstance(game, standard.StandardSlots) else (amount, 0, 0)


@pytest.mark.parametrize("rng,bet,symbol,payout", [
    (0.0, 10, "D", 50),
    (0.05, 10, "D", 50),
    (0.050000001, 10, "A", 15),
    (0.1, 3, "A", 4),
    (0.2, 10, "A", 15),
])
def test_standard_wins(context, monkeypatch, rng, bet, symbol, payout):
    monkeypatch.setattr(standard.random, "random", lambda: rng)
    game = standard.StandardSlots(context)

    assert game.settle(bet) == (
        (symbol,) * 3, payout, f"MATCH: +{payout} chips", True
    )
    # Standard wins credit the gain without first deducting the wager.
    assert context.account.balance == 100 + payout


@pytest.mark.parametrize("rng", [0.200000001, 0.9, 1.0])
def test_standard_loss_retries_matching_results(context, monkeypatch, rng):
    monkeypatch.setattr(standard.random, "random", lambda: rng)
    game = standard.StandardSlots(context)
    results = Mock(side_effect=[("D",) * 3, ("A",) * 3, LOSING_ROW])
    monkeypatch.setattr(game, "random_result", results)

    assert game.settle(10) == (LOSING_ROW, 0, "NO MATCH: -10 chips", False)
    assert results.call_count == 3
    assert context.account.balance == 90


@pytest.mark.parametrize("row,bet,payout", [
    (("D",) * 3, 10, 50),
    (("A",) * 3, 10, 15),
    (("B",) * 3, 3, 4),
    (("C",) * 3, 2, 3),
    (("D",) * 3, 0, 0),
    (("A",) * 3, 0, 0),
    (LOSING_ROW, 10, 0),
    (("A", "A", "B"), 10, 0),
    (("A", "B", "A"), 10, 0),
    (("B", "A", "A"), 10, 0),
])
def test_expanded_row_payout(row, bet, payout):
    assert expanded.payout_for_row(row, bet) == payout


@pytest.mark.parametrize("grid,bets,payout,winners", [
    (WINNING_GRID, (10, 3, 7), 54, [0, 1]),
    (WINNING_GRID, (0, 3, 7), 4, [1]),
    (LOSING_GRID, (10, 10, 10), 0, []),
    ((("D",) * 3,) * 3, (2, 3, 4), 45, [0, 1, 2]),
    ((("A",) * 3,) * 3, (0, 0, 2), 3, [2]),
    (WINNING_GRID, (0, 0, 0), 0, []),
])
def test_expanded_scoring_and_settlement(
    context, monkeypatch, grid, bets, payout, winners
):
    assert expanded.score_lines(grid, bets) == (payout, winners)
    game = expanded.ExpandedSlots(context)
    monkeypatch.setattr(game, "random_result", lambda: grid)
    rows = ",".join(str(i + 1) for i in winners)
    message = (f"ROWS {rows} MATCH: -{sum(bets)} +{payout} chips" if winners
               else f"NO MATCH: -{sum(bets)} chips")

    assert game.settle(bets) == (grid, payout, message, bool(winners))
    assert context.account.balance == 100 - sum(bets) + payout


@pytest.mark.parametrize("raw,expected", [("10", 10), ("2", 2), ("100", 100),
                                           (" 12 ", 12)])
def test_standard_valid_bets(context, terminal, raw, expected):
    script(terminal, raw)
    game = standard.StandardSlots(context)
    assert game.prompt_bet() == expected
    assert game.bet_total(expected) == expected
    assert context.account.balance == 100


@pytest.mark.parametrize("raw,message", [
    ("", "That's not a valid bet."),
    ("chips", "That's not a valid bet."),
    ("2.5", "That's not a valid bet."),
    ("-10", "Each pay line requires at least 2 chips."),
    ("0", "Each pay line requires at least 2 chips."),
    ("1", "Each pay line requires at least 2 chips."),
    ("101", "You only have 100 chips. Please try again."),
])
def test_standard_invalid_bets_retry(context, terminal, raw, message):
    prompt = script(terminal, raw, "10")
    assert standard.StandardSlots(context).prompt_bet() == 10
    assert prompt.call_count == 2
    terminal.output.assert_any_call(message)
    assert context.account.balance == 100


@pytest.mark.parametrize("raw,expected", [
    ("10,15,20", (10, 15, 20)),
    ("2,0,0", (2, 0, 0)),
    ("1,1,0", (1, 1, 0)),
    ("0,0,100", (0, 0, 100)),
    (" 10, 20, 30 ", (10, 20, 30)),
])
def test_expanded_valid_bets(context, terminal, raw, expected):
    script(terminal, raw)
    game = expanded.ExpandedSlots(context)
    assert game.prompt_bet() == expected
    assert game.bet_total(expected) == sum(expected)
    assert context.account.balance == 100


@pytest.mark.parametrize("raw,message", [
    ("", constants.INVALID_BET_MSG_LINES),
    ("10,10", constants.INVALID_BET_MSG_LINES),
    ("10,10,10,10", constants.INVALID_BET_MSG_LINES),
    ("10,,10", constants.INVALID_BET_MSG_LINES),
    ("10,no,10", constants.INVALID_BET_MSG_LINES),
    ("10,2.5,10", constants.INVALID_BET_MSG_LINES),
    ("-1,10,10", "Bets cannot be negative."),
    ("0,0,0", "You must bet at least 2 chips."),
    ("0,1,0", "You must bet at least 2 chips."),
    ("40,40,40", "You need 120 chips total. You have 100."),
])
def test_expanded_invalid_bets_retry(context, terminal, raw, message):
    prompt = script(terminal, raw, "2,3,4")
    assert expanded.ExpandedSlots(context).prompt_bet() == (2, 3, 4)
    assert prompt.call_count == 2
    terminal.output.assert_any_call(message)
    assert context.account.balance == 100


@pytest.mark.parametrize("choice,status,new_bet", [
    ("q", "EXIT", False), ("Q", "EXIT", False),
    ("r", "CONTINUE", False), ("R", "CONTINUE", False),
    ("c", "CONTINUE", True), ("C", "CONTINUE", True),
])
def test_round_menu_actions(game, terminal, choice, status, new_bet):
    raw = "10" if isinstance(game, standard.StandardSlots) else "10,0,0"
    script(terminal, raw, choice)
    assert game.play_round() == status
    assert game.take_new_bet is new_bet
    assert game.bet == bet_for(game, 10)
    assert game.account.balance == 90
    assert (game.stats.rounds_played, game.stats.wins, game.stats.losses) == (1, 0, 1)
    if status == "EXIT":
        assert game.stats.ending_balance == 90


@pytest.mark.parametrize("balance,answers,menu,choice", [
    (100, ["", "invalid", "q"], "[R]espin [C]hange Bet [Q]uit", "quit"),
    (5, ["r", "c"], "[C]hange Bet [Q]uit", "change_bet"),
    (1, ["r", "c", "q"], "[Q]uit", "quit"),
    (10, ["r"], "[R]espin [C]hange Bet [Q]uit", "respin"),
    (2, ["c"], "[C]hange Bet [Q]uit", "change_bet"),
])
def test_menu_restrictions(game, terminal, balance, answers, menu, choice):
    game.account.balance = balance
    prompt = script(terminal, *answers)
    result = LOSING_ROW if isinstance(game, standard.StandardSlots) else LOSING_GRID
    assert game.get_player_choice(result, 10) == choice
    assert prompt.call_count == len(answers)
    assert all(call.args == (menu,) for call in prompt.call_args_list)
    errors = [call for call in terminal.output.call_args_list
              if call.args == (constants.INVALID_INPUT_MSG,)]
    assert len(errors) == len(answers) - 1
    assert game.account.balance == balance


def test_round_without_funds_exits_without_spinning(game, terminal):
    game.account.balance = 1
    script(terminal, "")
    assert game.play_round() == "EXIT"
    terminal.output.assert_any_call(constants.NO_FUNDS_MSG)
    terminal.spin.assert_not_called()
    assert game.account.balance == game.stats.ending_balance == 1
    assert (game.stats.rounds_played, game.stats.wins, game.stats.losses) == (0, 0, 0)


def test_standing_bet_above_balance_requests_new_bet(game, terminal):
    game.account.balance = 5
    game.bet = bet_for(game, 10)
    game.take_new_bet = False
    assert game.play_round() == "CONTINUE"
    assert game.take_new_bet is True
    terminal.prompt.assert_not_called()
    terminal.spin.assert_not_called()
    assert game.account.balance == 5
    assert game.stats.rounds_played == 0

    raw = "2" if isinstance(game, standard.StandardSlots) else "2,0,0"
    script(terminal, raw, "q")
    assert game.play_round() == "EXIT"
    assert game.bet == bet_for(game, 2)
    assert game.account.balance == game.stats.ending_balance == 3
    assert (game.stats.rounds_played, game.stats.losses) == (1, 1)


@pytest.mark.parametrize("entrypoint,module,raw_bets,ending", [
    (play_slots, standard, ("10", "3"), 144),
    (play_slots_expanded, expanded, ("10,0,0", "3,0,0"), 131),
])
def test_public_session(context, terminal, monkeypatch, entrypoint, module,
                        raw_bets, ending):
    """Run real rounds, settlement, and the session summary with scripted input."""
    prompt = script(terminal, raw_bets[0], "r", "c", raw_bets[1], "q", "")
    # Win, respin and lose, then change the bet and win a rounded payout.
    monkeypatch.setattr(standard.random, "random", Mock(side_effect=[0, 0.9, 0.1]))
    monkeypatch.setattr(expanded, "get_rand_grid", Mock(side_effect=[
        WINNING_GRID, LOSING_GRID, (("A",) * 3,) * 3,
    ]))

    display = Mock(wraps=stats_module.display_stats)
    monkeypatch.setattr(module, "display_stats", display)
    entrypoint(context)

    assert context.account.balance == ending
    display.assert_called_once()
    stats = display.call_args.args[0]
    assert stats == stats_module.GameStats(
        game_name="Slots" if module is standard else "Slots (Expanded)",
        starting_balance=100,
        ending_balance=ending,
        rounds_played=3,
        wins=2,
        losses=1,
    )
    assert prompt.call_count == 6
    prompt.assert_called_with("Press Enter to return to menu...")
    assert terminal.spin.call_count == 3
    output = " ".join(" ".join(call.args[0].split())
                      for call in terminal.output.call_args_list)
    assert "Session Summary" in output
    assert f"Ending Balance: {ending}" in output
    assert f"Net Profit/Loss: +{ending - 100}" in output
    assert "Win Rate: 66.7%" in output
