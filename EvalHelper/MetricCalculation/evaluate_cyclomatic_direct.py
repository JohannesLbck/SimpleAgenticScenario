import glob
import os
import sys
import xml.etree.ElementTree as ET


NS_DESC = "http://cpee.org/ns/description/1.0"
STRUCTURAL_TAGS = {
    "description",
    "call",
    "parallel",
    "parallel_branch",
    "choose",
    "alternative",
    "otherwise",
    "loop",
    "terminate",
    "stop",
}
SEQUENCE_WRAPPER_TAGS = {"description", "parallel_branch", "alternative", "otherwise"}
ACTIVITY_TAGS = {"call", "terminate", "stop"}


class Segment:
    """Compact summary of a structured fragment.

    entries/exits are counts of entry/exit frontier nodes needed to connect fragments
    without materializing the whole graph.
    """

    def __init__(self, entries=0, exits=0, nodes=0, edges=0):
        self.entries = entries
        self.exits = exits
        self.nodes = nodes
        self.edges = edges


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


def _structural_children(elem):
    return [c for c in elem if _strip_ns(c.tag) in STRUCTURAL_TAGS]


def _sequence_of(elements):
    result = Segment(entries=0, exits=0, nodes=0, edges=0)
    prev_exits = None

    for child in elements:
        tag = _strip_ns(child.tag)
        if tag not in STRUCTURAL_TAGS:
            continue

        seg = _segment_of(child)
        if seg.entries == 0 and seg.exits == 0 and seg.nodes == 0:
            continue

        result.nodes += seg.nodes
        result.edges += seg.edges

        if result.entries == 0:
            result.entries = seg.entries
        if prev_exits is not None:
            result.edges += prev_exits * seg.entries

        prev_exits = seg.exits

    result.exits = prev_exits if prev_exits is not None else 0
    return result


def _segment_of(elem):
    tag = _strip_ns(elem.tag)

    if tag in ACTIVITY_TAGS:
        return Segment(entries=1, exits=1, nodes=1, edges=0)

    if tag in SEQUENCE_WRAPPER_TAGS:
        return _sequence_of(_structural_children(elem))

    if tag == "parallel":
        branches = [c for c in elem if _strip_ns(c.tag) == "parallel_branch"]
        seg = Segment(entries=1, exits=1, nodes=2, edges=0)  # split + join

        if not branches:
            seg.edges += 1  # split -> join
            return seg

        for br in branches:
            body = _sequence_of(_structural_children(br))
            seg.nodes += body.nodes
            seg.edges += body.edges
            if body.entries > 0:
                seg.edges += body.entries  # split -> branch entries
                seg.edges += body.exits    # branch exits -> join
            else:
                seg.edges += 1             # empty branch

        return seg

    if tag == "choose":
        branches = [
            c for c in elem if _strip_ns(c.tag) in {"alternative", "otherwise"}
        ]
        seg = Segment(entries=1, exits=1, nodes=2, edges=0)  # split + join

        if not branches:
            seg.edges += 1
            return seg

        for br in branches:
            body = _sequence_of(_structural_children(br))
            seg.nodes += body.nodes
            seg.edges += body.edges
            if body.entries > 0:
                seg.edges += body.entries
                seg.edges += body.exits
            else:
                seg.edges += 1

        return seg

    if tag == "loop":
        body = _sequence_of(_structural_children(elem))
        seg = Segment(entries=1, exits=1, nodes=2 + body.nodes, edges=1 + body.edges)
        if body.entries > 0:
            seg.edges += body.entries  # decision -> body entries
            seg.edges += body.exits    # body exits -> decision (back-edge)
        return seg

    return Segment()


def _count_calls(tree):
    return sum(1 for elem in tree.iter() if _strip_ns(elem.tag) == "call")


def cyclomatic_complexity_direct(tree):
    top = _sequence_of(_structural_children(tree))
    e = top.edges
    n = top.nodes
    p = _count_calls(tree)
    m = e - n + 2 * p
    return {"E": e, "N": n, "P": p, "M": m}


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
            values = cyclomatic_complexity_direct(tree)
            print(
                f"{os.path.basename(xml_path)}: "
                f"E={values['E']} N={values['N']} P={values['P']} M={values['M']}"
            )
        except Exception as exc:
            print(f"{os.path.basename(xml_path)}: ERROR: {exc}")


if __name__ == "__main__":
    main()
