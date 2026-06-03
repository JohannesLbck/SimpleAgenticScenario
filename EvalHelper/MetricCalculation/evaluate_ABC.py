import glob
import math
import os
import sys
import xml.etree.ElementTree as ET


NS_DESC = "http://cpee.org/ns/description/1.0"



A_cpee = {
    "manipulate",
    "terminate",
    "stop",
}
B_cpee = {
    "call",
} 
C_cpee = { 
    "parallel_branch"
    "alternative",
    "otherwise",
    "loop",
}

A = {
    "manipulate",
}
B = {
    "call",
} 
C = { 
    "choose",
    "loop",
    "parallel",
}


def _strip_ns(tag):
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def get_process_tree(xml_path):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    desc = root.find(".//{%s}description" % NS_DESC)
    if desc is None:
        raise ValueError(f"No inner <description> element found in {xml_path}")
    return desc


def _count_by_tag_set(tree, tag_set):
    """Count XML nodes whose local tag is included in *tag_set*."""
    return sum(1 for elem in tree.iter() if _strip_ns(elem.tag) in tag_set)


def _count_assignments(tree):
    return _count_by_tag_set(tree, A)


def _count_branches(tree):
    return _count_by_tag_set(tree, B)


def _count_conditionals(tree):
    return _count_by_tag_set(tree, C)


def abc_metric(tree):
    a = _count_assignments(tree)
    b = _count_branches(tree)
    c = _count_conditionals(tree)
    scalar = math.sqrt((a * a) + (b * b) + (c * c))
    return {
        "A": a,
        "B": b,
        "C": c,
        "vector": f"<{a},{b},{c}>",
        "scalar": scalar,
    }


def _resolve_inputs(argv):
    if argv:
        return argv
    return sorted(glob.glob("*.xml"))


def main():
    paths = _resolve_inputs(sys.argv[1:])
    if not paths:
        print("No XML files found. Pass paths as arguments or run in a folder containing *.xml.")
        return

    for xml_path in paths:
        try:
            tree = get_process_tree(xml_path)
            values = abc_metric(tree)
            print(
                f"{os.path.basename(xml_path)}: "
                f"ABC vector={values['vector']} "
                f"ABC scalar={values['scalar']:.4f}"
            )
        except Exception as exc:
            print(f"{os.path.basename(xml_path)}: ERROR: {exc}")


if __name__ == "__main__":
    main()
