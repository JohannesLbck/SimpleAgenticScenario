import glob
import os
import sys
import xml.etree.ElementTree as ET


NS_DESC = "http://cpee.org/ns/description/1.0"
STRUCTURAL_TAGS = {
    "description",
    "call",
    "manipulate",
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
ACTIVITY_TAGS = {"call", "manipulate", "terminate", "stop"}


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


def _choose_type(elem):
    mode = (elem.attrib.get("mode") or "").strip().lower()
    if mode in {"inclusive", "or"}:
        return "OR"
    return "XOR"


def _build_flow_graph(tree):
    """Build a directed flow graph compatible with the evaluate.py approach."""
    next_id = [0]
    nodes = set()
    edges = set()

    def add_node(_kind):
        next_id[0] += 1
        nid = f"n{next_id[0]}"
        nodes.add(nid)
        return nid

    def add_edge(src, dst):
        if src is not None and dst is not None:
            edges.add((src, dst))

    def connect_all(from_nodes, to_nodes):
        for src in from_nodes:
            for dst in to_nodes:
                add_edge(src, dst)

    def branch_content(branch_elem):
        return [c for c in branch_elem if _strip_ns(c.tag) in STRUCTURAL_TAGS]

    def build_sequence(elements):
        entries = set()
        prev_exits = None

        for child in elements:
            tag = _strip_ns(child.tag)
            if tag not in STRUCTURAL_TAGS:
                continue

            child_entries, child_exits = build_elem(child)
            if not child_entries and not child_exits:
                continue

            if not entries:
                entries = set(child_entries)
            if prev_exits is not None:
                connect_all(prev_exits, child_entries)
            prev_exits = set(child_exits)

        if not entries:
            return set(), set()
        return entries, (prev_exits if prev_exits is not None else set())

    def build_elem(elem):
        tag = _strip_ns(elem.tag)

        if tag in ACTIVITY_TAGS:
            nid = add_node("activity")
            return {nid}, {nid}

        if tag in SEQUENCE_WRAPPER_TAGS:
            return build_sequence(branch_content(elem))

        if tag == "parallel":
            split = add_node("and_split")
            join = add_node("and_join")

            branches = [c for c in elem if _strip_ns(c.tag) == "parallel_branch"]
            if not branches:
                add_edge(split, join)
                return {split}, {join}

            for br in branches:
                b_entries, b_exits = build_sequence(branch_content(br))
                if b_entries:
                    connect_all({split}, b_entries)
                    connect_all(b_exits, {join})
                else:
                    add_edge(split, join)

            return {split}, {join}

        if tag == "choose":
            _choose_type(elem)
            split = add_node("choice_split")
            join = add_node("choice_join")

            branches = [
                c for c in elem if _strip_ns(c.tag) in {"alternative", "otherwise"}
            ]
            if not branches:
                add_edge(split, join)
                return {split}, {join}

            for br in branches:
                b_entries, b_exits = build_sequence(branch_content(br))
                if b_entries:
                    connect_all({split}, b_entries)
                    connect_all(b_exits, {join})
                else:
                    add_edge(split, join)

            return {split}, {join}

        if tag == "loop":
            decision = add_node("loop_decision")
            after_loop = add_node("loop_exit")
            add_edge(decision, after_loop)

            body_entries, body_exits = build_sequence(branch_content(elem))
            if body_entries:
                connect_all({decision}, body_entries)
                connect_all(body_exits, {decision})

            return {decision}, {after_loop}

        return set(), set()

    root_children = [c for c in tree if _strip_ns(c.tag) in STRUCTURAL_TAGS]
    build_sequence(root_children)

    return {"nodes": nodes, "edges": edges}


def _count_calls(tree):
    return sum(1 for elem in tree.iter() if _strip_ns(elem.tag) == "call")


def cyclomatic_complexity_cfg(tree):
    graph = _build_flow_graph(tree)
    e = len(graph["edges"])
    n = len(graph["nodes"])
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
            values = cyclomatic_complexity_cfg(tree)
            print(
                f"{os.path.basename(xml_path)}: "
                f"E={values['E']} N={values['N']} P={values['P']} M={values['M']}"
            )
        except Exception as exc:
            print(f"{os.path.basename(xml_path)}: ERROR: {exc}")


if __name__ == "__main__":
    main()
