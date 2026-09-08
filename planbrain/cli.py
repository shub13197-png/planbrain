"""The command a person types.

**The package installed and offered nothing to run.** `planbrain.backend` is a
JSON-RPC server that speaks stdio to the desktop shell -- correct for the shell,
useless as a human interface -- and everything else lived in `tools/`, which is
outside the shipped package. So `pip install planbrain` produced a library and
no way to plan with it.

This is deliberately small. It is not a second interface to maintain beside the
window: it loads or builds data, runs the plan, and prints what to order. The
desktop application is still where a planner works. This is what makes the
product runnable on a machine with Python and no Rust toolchain, and what makes
`pip install` mean something.

    planbrain where                which file your data is in
    planbrain demo                 build the worked example into that file
    planbrain plan                 run the plan
    planbrain orders --limit 20    what to order, soonest first
"""

import argparse
import sys

from .backend import api


def _open(args):
    return api.session(args.db or str(api.default_database()))


def _needs_data(state) -> bool:
    if state.demo is None:
        print("No dataset in this database yet. Run `planbrain demo` for the worked "
              "example, or import your own data in the desktop application.",
              file=sys.stderr)
        return True
    return False


def cmd_where(args) -> int:
    """Where the data is. "Where did my data go" is otherwise unanswerable."""
    path = args.db or str(api.default_database())
    state = api.session(path)
    loaded = state.demo is not None
    parts = len(state.demo.parts) if loaded else 0
    state.con.close()
    print(path)
    print(f"  dataset: {str(parts) + ' parts' if loaded else 'none yet'}")
    return 0


def cmd_demo(args) -> int:
    state = _open(args)
    with state.con:
        result = api.demo_build(state, seed=args.seed)
    state.con.close()
    print(f"Built the worked example: {result['parts']} parts, "
          f"{result['bom_edges']} BOM links, {result['trucks']} trucks.")
    print("It is saved. Run `planbrain plan` next.")
    return 0


def cmd_plan(args) -> int:
    state = _open(args)
    if _needs_data(state):
        state.con.close()
        return 2
    with state.con:
        result = api.plan_run(state, source=args.source, lot_sizing=args.lot_sizing)
    capacity = result["capacity"]
    print("The plan fits." if capacity["feasible"] else "The plan does NOT fit.")
    print(f"  {capacity['utilisation']:.0%} overall utilisation, "
          f"{capacity['load_hours']:,.0f} h of work against "
          f"{capacity['capacity_hours']:,.0f} h available")
    if capacity["overloaded_buckets"]:
        print(f"  {capacity['overloaded_buckets']} overloaded days -- days where the plan "
              f"asks for more hours than exist. This reports the problem; it does not "
              f"choose what to move.")
    state.con.close()
    print("Run `planbrain orders` for the list to act on.")
    return 0


def cmd_orders(args) -> int:
    state = _open(args)
    if _needs_data(state):
        state.con.close()
        return 2
    result = api.plan_orders(state, limit=args.limit, action=args.action)
    state.con.close()
    print(f"{result['shown']} of {result['total']} planned releases "
          f"({result['totals']['make']} make, {result['totals']['buy']} buy)")
    print(f"{'release':<12} {'item':<28} {'action':<7} {'qty':>12}  where")
    for order in result["orders"]:
        print(f"{order['release_date']:<12} {order['name'][:28]:<28} "
              f"{order['action']:<7} {order['qty']:>12,.0f}  {order['location']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="planbrain", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=None,
                        help="database file (default: the per-user file docs/install.md names)")
    sub = parser.add_subparsers(dest="command", required=True)

    where = sub.add_parser("where", help="which file your data is in")
    where.set_defaults(func=cmd_where)

    demo = sub.add_parser("demo", help="build the worked example")
    demo.add_argument("--seed", type=int, default=7)
    demo.set_defaults(func=cmd_demo)

    plan = sub.add_parser("plan", help="run the plan")
    plan.add_argument("--source", default="forecast", choices=["forecast", "naive_replay"])
    plan.add_argument("--lot-sizing", dest="lot_sizing", default="cost_based",
                      choices=["cost_based", "as_master"])
    plan.set_defaults(func=cmd_plan)

    orders = sub.add_parser("orders", help="what to order")
    orders.add_argument("--limit", type=int, default=20)
    orders.add_argument("--action", default=None, choices=["make", "buy"])
    orders.set_defaults(func=cmd_orders)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
