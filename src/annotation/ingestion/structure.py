"""Build a source-position tree, without asking a model to invent book contents."""

import hashlib
import re
from collections import Counter

from annotation.domain.artifacts import SourceBlock, SourceDocument
from annotation.domain.textbook import SourceRange, StructureNode, TextbookStructure


def _heading(text: str) -> tuple[int, str] | None:
    title = text.splitlines()[0].strip() if text.strip() else ""
    if not title or len(title) > 100:
        return None
    if re.match(r"^第[零〇一二三四五六七八九十百千\d]+[篇部章](?:\s|$)", title):
        return 1, title
    if re.match(r"^第[零〇一二三四五六七八九十百千\d]+节(?:\s|$)", title):
        return 2, title
    match = re.match(r"^(#{1,6})\s+(.+)", title)
    if match:
        return len(match[1]), match[2]
    match = re.match(r"^(?:§\s*)?(\d+(?:\.\d+){1,4})[.\s、]+([^=<>≤≥]+)$", title)
    if match and not re.match(r"^[\d\W]+$", match[2]):
        return match[1].count(".") + 1, title
    match = re.match(r"^(?:Chapter|CHAPTER)\s+\d+\b.*", title)
    return (1, title) if match else None


def _normalized(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).casefold()


def build_textbook_structure(document: SourceDocument, blocks: list[SourceBlock]) -> TextbookStructure:
    ordered = sorted(blocks, key=lambda b: (b.page_number, b.block_index))
    refs = [b.source_ref for b in ordered]
    if len(set(refs)) != len(refs):
        raise ValueError("duplicate source_ref in textbook")
    warnings: list[str] = []
    candidates: dict[int, tuple[int, str, str]] = {}
    page_positions: dict[int, list[int]] = {}
    for index, block in enumerate(ordered):
        page_positions.setdefault(block.page_number, []).append(index)
    # Bookmarks are captured by the parser while the PDF is open. Never reopen
    # an unrelated path on fixture/preloaded imports.
    bookmarks = document.run_metadata.get("toc", [])
    # Export tools often generate one bookmark per scanned page, using the
    # filename plus an increasing page number instead of actual chapter titles.
    numbered = {}
    for bookmark in bookmarks:
        if (isinstance(bookmark, (list, tuple)) and len(bookmark) >= 3
                and isinstance(bookmark[0], int) and isinstance(bookmark[2], int)
                and bookmark[0] > 0 and bookmark[2] > 0):
            match = re.fullmatch(r"(.{8,}?)\s+(\d+)", str(bookmark[1]).strip())
            if match and _heading(match[1]) is None:
                numbered.setdefault((bookmark[0], match[1]), []).append((bookmark[2], int(match[2])))
    page_series = {key for key, entries in numbered.items() if len(entries) >= 3
                   and len({page for page, _ in entries}) == len(entries)
                   and len({number - page for page, number in entries
                            if isinstance(page, int)}) == 1}
    for bookmark in bookmarks:
        if not isinstance(bookmark, (list, tuple)) or len(bookmark) < 3:
            warnings.append("invalid_bookmark")
            continue
        level, title, page = bookmark[:3]
        if not isinstance(level, int) or not isinstance(page, int) or level < 1 or page < 1:
            warnings.append("invalid_bookmark")
            continue
        match = re.fullmatch(r"(.{8,}?)\s+(\d+)", str(title).strip())
        positions = page_positions.get(page, [])
        if not positions:
            warnings.append(f"bookmark_without_text:{page}:{title}")
            continue
        matches = [i for i in positions if _normalized(str(title)) in _normalized(ordered[i].text)]
        if not matches and match and (level, match[1]) in page_series:
            warnings.append(f"ignored_page_bookmark:{page}:{title}")
            continue
        position = matches[0] if matches else positions[0]
        if not matches:
            warnings.append(f"bookmark_page_fallback:{page}:{title}")
        if position in candidates:
            warnings.append(f"bookmark_same_block:{page}:{title}")
            continue
        candidates[position] = (level, str(title), "bookmark")
    headings = [_heading(b.text) for b in ordered]
    frequencies = Counter(h[1] for h in headings if h)
    seen: set[str] = set()
    for index, heading in enumerate(headings):
        if heading is None or index in candidates:
            continue
        level, title = heading
        if frequencies[title] > 2:
            if title in seen:
                continue  # repeated running header; retain the first occurrence
            seen.add(title)
        candidates[index] = (level, title, "heading")
    root_id = f"tree-{hashlib.sha256(document.artifact_id.encode()).hexdigest()[:12]}"
    root = StructureNode(node_id=root_id, title=document.title, level=0, kind="book", origin="document", start=0, end=len(ordered))
    nodes = [root]
    stack = [root]
    owner: list[StructureNode] = []
    for index, block in enumerate(ordered):
        if index in candidates:
            level, title, origin = candidates[index]
            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop().end = index
            node_id = "section-" + hashlib.sha256(f"{document.artifact_id}:{block.source_ref}:{title}".encode()).hexdigest()[:16]
            node = StructureNode(node_id=node_id, parent_id=stack[-1].node_id, title=title, level=level, kind="chapter" if level == 1 else "section", origin=origin, start=index, end=len(ordered))
            nodes.append(node)
            stack.append(node)
        owner.append(stack[-1])
    for index, node in enumerate(owner):
        if node.owned_ranges and node.owned_ranges[-1].end == index:
            node.owned_ranges[-1].end = index + 1
        else:
            node.owned_ranges.append(SourceRange(start=index, end=index + 1))
    # A parent heading immediately followed by its first subheading belongs
    # with that child's material. Keep the structural node, and every source ref.
    for node in nodes:
        children = [child for child in nodes if child.parent_id == node.node_id]
        if not children or not node.owned_ranges:
            continue
        first = children[0]
        indices = [i for span in node.owned_ranges for i in range(span.start, span.end)]
        if indices and indices[-1] + 1 == first.start and all(
                _heading(ordered[i].text) is not None
                and len(ordered[i].text.strip().splitlines()) == 1 for i in indices):
            first.owned_ranges = [*node.owned_ranges, *first.owned_ranges]
            node.owned_ranges = []
    if len(nodes) == 1:
        warnings.append("unsectioned_textbook")
    warnings.extend(document.warnings)
    for page in document.run_metadata.get("empty_text_pages", []):
        warnings.append(f"page_without_extracted_text:{page}")
    return TextbookStructure(document_id=document.artifact_id, ordered_source_refs=refs, nodes=nodes, warnings=warnings)
