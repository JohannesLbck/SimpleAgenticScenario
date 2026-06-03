import os
import sys

from evaluate_ABC import abc_metric
from evaluate_cyclomatic_cfg import cyclomatic_complexity_cfg, get_process_tree as get_tree_cfg
from evaluate_cyclomatic_direct import cyclomatic_complexity_direct


def _resolve_inputs(argv):
    return argv


def _fmt(values):
    return f"E={values['E']} N={values['N']} P={values['P']} M={values['M']}"


def main():
    paths = _resolve_inputs(sys.argv[1:])
    if not paths:
        print("No XML files provided. Usage: python evaluate_all.py <file1.xml> [file2.xml ...]")
        return

    for xml_path in paths:
        label = os.path.basename(xml_path)

        if not os.path.exists(xml_path):
            print(f"{label}: ERROR: File not found")
            continue

        try:
            tree = get_tree_cfg(xml_path)
            cfg_values = cyclomatic_complexity_cfg(tree)
            direct_values = cyclomatic_complexity_direct(tree)
            abc_values = abc_metric(tree)
        except Exception as exc:
            print(f"{label}: ERROR: {exc}")
            continue

        print(f"\n{label}")
        print(f"  CFG    : {_fmt(cfg_values)}")
        print(f"  Direct : {_fmt(direct_values)}")
        print(
            "  ABC    : "
            f"vector={abc_values['vector']} scalar={abc_values['scalar']:.4f}"
        )

        if cfg_values == direct_values:
            print("  Result : MATCH")
        else:
            print("  Result : MISMATCH")
            for key in ("E", "N", "P", "M"):
                if cfg_values[key] != direct_values[key]:
                    print(
                        "    "
                        f"{key}: cfg={cfg_values[key]} direct={direct_values[key]}"
                    )


if __name__ == "__main__":
    main()
