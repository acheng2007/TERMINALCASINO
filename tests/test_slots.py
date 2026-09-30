"""Deterministic settlement, input, and session tests for both slots variants."""

from itertools import cycle
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
    """Isolate terminal effects and make every random source deterministic."""
    output = Mock()
    prompt = Mock(side_effect=AssertionError("Unexpected input request"))
    monkeypatch.setattr(view, "cinput", prompt)
    monkeypatch.setattr(view, "cprint", output)
    monkeypatch.setattr(view, "clear_screen", Mock())
    monkeypatch.setattr(view, "display_topbar", Mock())
    monkeypatch.setattr(view.SlotsView, "render_row", Mock())
    monkeypatch.setattr(view.SlotsView, "render_grid", Mock())
    monkeypatch.setattr(base.time, "sleep", Mock())
    monkeypatch.setattr(stats_module, "cinput", prompt)
    monkeypatch.setattr(stats_module, "cprint", output)
    monkeypatch.setattr(stats_module, "clear_screen", Mock())
    symbols = cycle(constants.ALL_ITEMS)
    monkeypatch.setattr(standard.random, "choice", lambda items: next(symbols))
    monkeypatch.setattr(standard.random, "random", Mock(return_value=0.9))
    monkeypatch.setattr(standard.random, "randint", Mock(return_value=0))
    return prompt, output


@pytest.fixture(params=[standard.StandardSlots, expanded.ExpandedSlots],
                ids=["standard", "expanded"])
def game(request, context):
    return request.param(context)


def script(terminal, *answers):
    prompt, _ = terminal
    prompt.side_effect = answers
    return prompt


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
    terminal[1].assert_any_call(message)
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
    terminal[1].assert_any_call(message)
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
    errors = [call for call in terminal[1].call_args_list
              if call.args == (constants.INVALID_INPUT_MSG,)]
    assert len(errors) == len(answers) - 1
    assert game.account.balance == balance


def test_round_without_funds_exits_without_spinning(game, terminal):
    game.account.balance = 1
    script(terminal, "")
    assert game.play_round() == "EXIT"
    terminal[1].assert_any_call(constants.NO_FUNDS_MSG)
    base.time.sleep.assert_not_called()
    assert game.account.balance == game.stats.ending_balance == 1
    assert (game.stats.rounds_played, game.stats.wins, game.stats.losses) == (0, 0, 0)


def test_standing_bet_above_balance_requests_new_bet(game, terminal):
    game.account.balance = 5
    game.bet = bet_for(game, 10)
    game.take_new_bet = False
    assert game.play_round() == "CONTINUE"
    assert game.take_new_bet is True
    terminal[0].assert_not_called()
    base.time.sleep.assert_not_called()
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
    """Run real rounds, settlement, animation loops, and the session summary."""
    prompt = script(terminal, raw_bets[0], "r", "c", raw_bets[1], "q", "")
    if module is standard:
        monkeypatch.setattr(standard.random, "random", Mock(side_effect=[0, 0.9, 0.1]))
    else:
        # Each round consumes animation grids before its settlement grid.
        frames = 5 + constants.TOTAL_SPINS
        grids = []
        for final in (WINNING_GRID, LOSING_GRID, (("A",) * 3,) * 3):
            grids.extend([LOSING_GRID] * frames + [final])
        monkeypatch.setattr(expanded, "get_rand_grid", Mock(side_effect=grids))

    display = Mock(wraps=stats_module.display_stats)
    monkeypatch.setattr(module, "display_stats", display)
    entrypoint(context)

    assert context.account.balance == ending
    display.assert_called_once()
    stats = display.call_args.args[0]
    assert stats.starting_balance == 100
    assert stats.ending_balance == ending
    assert (stats.rounds_played, stats.wins, stats.losses, stats.pushes) == (3, 2, 1, 0)
    assert stats.net == ending - 100
    assert stats.win_rate == "66.7%"
    assert stats.game_name == ("Slots" if module is standard else "Slots (Expanded)")
    assert prompt.call_count == 6
    prompt.assert_called_with("Press Enter to return to menu...")
    assert base.time.sleep.call_count == 3 * (5 + constants.TOTAL_SPINS)
    output = "\n".join(call.args[0] for call in terminal[1].call_args_list)
    for label, value in [("Game", stats.game_name), ("Hands Played", "3"),
                         ("Wins", "2"), ("Losses", "1"), ("Win Rate", "66.7%"),
                         ("Starting Balance", "100"), ("Ending Balance", str(ending)),
                         ("Net Profit/Loss", f"+{ending - 100}")]:
        assert any(label + ":" in line and value in line for line in output.splitlines())
