"""Compare our planner with the reference engine (Paccurate) per case.

Each case folder has the engine's request (input.json) and response
(output.json). We compare box count and box types: fewer leftover units wins,
then fewer boxes, then less total box volume.

    python evaluate.py                       # parse cases, show references
    python evaluate.py --model gpt-5.4-mini  # also run ours (costs tokens)
    python evaluate.py --openrouter --model qwen/qwen3.8-27b:free

Axes match ours: the engine's X is height (its schema: "the 'up' direction
is X"). Its weightMax is gross (items + weightTare), so our contents limit is
weightMax - weightTare. Engine-only options (rules, cohortPacking, rate
tables, usableSpace, ...) are listed as notes, not modelled.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

ENGINE_ONLY = ["rules", "boxTypeSets", "usableSpace", "cohortPacking",
               "allowableOverhang", "layFlat", "interlock", "zone",
               "boxTypeChoiceGoal"]


def _dims(d) -> dict:
    return {"x": d["x"], "y": d["y"], "z": d["z"]}


def _box_key(name: str, dims: dict) -> tuple:
    return name, tuple(sorted(dims.values()))


def load_case(folder: Path) -> dict:
    request = json.loads((folder / "input.json").read_text())
    response = json.loads((folder / "output.json").read_text())

    items = [
        {
            "id": s["refId"],
            "name": s.get("name") or f"item {s['refId']}",
            "color": s.get("color") or "",
            "weight": s.get("weight") or 0,
            "dimensions": _dims(s["dimensions"]),
            "quantity": s["quantity"],
        }
        for s in request["itemSets"]
    ]

    # Box types: listed ones, plus pre-set boxes; "leftovers" is the engine's
    # overflow bucket, not a real box.
    raw = list(request.get("boxTypes", []))
    raw += [b["box"]["boxType"] for b in request.get("boxes", [])]
    refs = {s["refId"] for s in request["itemSets"]}
    request = dict(request, rules=[  # only rules about items in this case
        r for r in request.get("rules", []) if r.get("itemRefId") in refs])
    notes = [f"{k}={request[k]!r}" for k in ENGINE_ONLY
             if request.get(k) not in (None, [], False)]
    packed = Counter(it["item"]["refId"] for b in response["boxes"]
                     for it in b["box"]["items"])
    wanted = Counter({s["refId"]: s["quantity"] for s in request["itemSets"]})
    if packed != wanted:
        notes.append(f"OUTPUT DOES NOT MATCH INPUT: requested {dict(wanted)}, "
                     f"output packs {dict(packed)}")
    # The box list lives in the engine, so there's nothing fair to compare.
    skip = None
    if request.get("boxTypeGenerators"):
        skip = "engine generates its own box sizes (boxTypeGenerators)"
    elif request.get("boxTypeSets"):
        skip = "box catalog lives in the engine (boxTypeSets)"
    elif request["rules"]:
        ops = sorted({r.get("operation") for r in request["rules"]})
        skip = f"engine rules on these items: {', '.join(ops)}"
    elif (request.get("boxTypeChoiceGoal", "lowest-cost") == "lowest-cost"
          and any(b.get("rateTable") or b.get("price") for b in raw)):
        skip = "boxes chosen by shipping cost (lowest-cost with rate tables)"
    boxes, seen = [], set()
    for b in raw:
        if b["name"] == "leftovers":
            continue
        key = _box_key(b["name"], b["dimensions"])
        if key in seen:
            continue
        seen.add(key)
        gross = b.get("weightMax")
        boxes.append({
            "id": len(boxes),
            "name": b["name"],
            "dimensions": _dims(b["dimensions"]),
            "weightMax": None if gross is None
            else gross - (b.get("weightTare") or 0),
        })

    return {"name": folder.name, "items": items, "boxes": boxes,
            "reference": reference_summary(response), "notes": notes,
            "skip": skip}


def _volume(dims: dict) -> float:
    return dims["x"] * dims["y"] * dims["z"]


def reference_summary(response: dict) -> dict:
    used, leftover = [], response.get("lenLeftovers", 0)
    for b in response["boxes"]:
        box = b["box"]
        if box["name"] == "leftovers":  # overflow bucket = unpacked units
            leftover += box["lenUnits"]
        else:
            used.append((box["name"], box["dimensions"]))
    return {
        "boxes": len(used),
        "types": dict(Counter(name for name, _ in used)),
        "volume": round(sum(_volume(d) for _, d in used), 2),
        "leftover_units": leftover,
    }


def our_summary(flat, boxes: list[dict]) -> dict:
    by_id = {b["id"]: b for b in boxes}
    used = [by_id[b.id] for b in flat.boxes]
    return {
        "boxes": len(used),
        "types": dict(Counter(b["name"] for b in used)),
        "volume": round(sum(_volume(b["dimensions"]) for b in used), 2),
        "leftover_units": sum(r.quantity for r in flat.leftovers),
    }


def _rank(summary: dict) -> tuple:
    return summary["leftover_units"], summary["boxes"], summary["volume"]


def verdict(ours: dict, ref: dict) -> str:
    key = _rank
    if key(ours) == key(ref):
        return "same"
    return "better" if key(ours) < key(ref) else "worse"


def _types(types: dict) -> str:
    return ", ".join(f"{name} ×{n}" for name, n in types.items()) or "—"


def comparison_table(rows: list[dict]) -> str:
    """Markdown table: reference vs ours per case."""
    lines = [
        "| Case | Ref boxes | Ref box types | Ref leftovers | Our boxes | "
        "Our box types | Our leftovers | Same types | Verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        ref, ours = r["reference"], r.get("ours")
        if ours:
            same = "yes" if set(ours["types"]) == set(ref["types"]) else "no"
            mine = (ours["boxes"], _types(ours["types"]),
                    ours["leftover_units"])
        else:
            same, mine = "—", ("—", "—", "—")
        lines.append(
            f"| {r['case']} | {ref['boxes']} | {_types(ref['types'])} | "
            f"{ref['leftover_units']} | {mine[0]} | {mine[1]} | {mine[2]} | "
            f"{same} | {r.get('verdict', 'not run')} |"
        )
    return "\n".join(lines)


def run_ours(case: dict, client, model: str, **kwargs):
    from planner import plan_with_validation
    from prompt_builder import SYSTEM_PROMPT, build_user_prompt

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",
         "content": build_user_prompt(case["items"], case["boxes"])},
    ]
    return plan_with_validation(client, model, messages, case["items"],
                                case["boxes"], **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="run our planner with this model")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--openrouter", action="store_true",
                        help="use OpenRouter (OPENROUTER_API_KEY in .env)")
    parser.add_argument("cases", nargs="*", help="case folders (default all)")
    args = parser.parse_args()

    folders = [HERE / c for c in args.cases] or sorted(
        p for p in HERE.iterdir() if (p / "input.json").exists())
    client = None
    if args.model:
        import os
        from dotenv import load_dotenv
        from openai import OpenAI
        load_dotenv(HERE.parent.parent / ".env")
        if args.openrouter:
            client = OpenAI(base_url="https://openrouter.ai/api/v1",
                            api_key=os.getenv("OPENROUTER_API_KEY"))
            # OpenRouter takes effort under `reasoning`; require_parameters
            # routes only to providers that support structured outputs
            call_kwargs = {"extra_body": {
                "reasoning": {"effort": args.effort},
                "provider": {"require_parameters": True}}}
        else:
            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            call_kwargs = {"reasoning_effort": args.effort}

    seen_inputs: dict[str, str] = {}
    results = []
    for folder in folders:
        case = load_case(folder)
        request = (folder / "input.json").read_text()
        key = json.dumps(json.loads(request), sort_keys=True)
        if key in seen_inputs:
            case["notes"].append(f"DUPLICATE of case '{seen_inputs[key]}' "
                                 "(same input)")
        seen_inputs.setdefault(key, case["name"])
        ref = case["reference"]
        units = sum(i["quantity"] for i in case["items"])
        print(f"\n== {case['name']}: {len(case['items'])} item types, "
              f"{units} units, {len(case['boxes'])} box types")
        for note in case["notes"]:
            print(f"   note: {note}")
        print(f"   reference: {ref['boxes']} boxes {ref['types']}, "
              f"leftover units {ref['leftover_units']}")
        row = {"case": case["name"], "reference": ref}
        if case["skip"]:
            print(f"   SKIPPED: {case['skip']}")
            results.append(dict(row, verdict="skipped"))
            continue
        if client:
            result = run_ours(case, client, args.model, **call_kwargs)
            if result.flat is None:
                print(f"   ours: no plan ({result.errors[:1]})")
                row["verdict"] = "failed"
            else:
                ours = our_summary(result.flat, case["boxes"])
                row.update(ours=ours, valid=result.ok,
                           verdict=verdict(ours, ref) if result.ok
                           else "invalid")
                print(f"   ours:      {ours['boxes']} boxes {ours['types']}, "
                      f"leftover units {ours['leftover_units']}, "
                      f"valid={result.ok} -> {row['verdict'].upper()}")
        results.append(row)

    table = comparison_table(results)
    print("\n" + table)
    if client:
        slug = args.model.replace("/", "_")  # OpenRouter ids have a slash
        out = HERE / f"results-{slug}.json"
        out.write_text(json.dumps(results, indent=2))
        (HERE / f"results-{slug}.md").write_text(table + "\n")
        print(f"\nsaved {out.name} and {out.stem}.md: "
              f"{dict(Counter(r['verdict'] for r in results))}")


if __name__ == "__main__":
    main()
