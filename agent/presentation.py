import re


def without_generated_sources(content):
    heading = re.search(
        r"(?im)^\s*(?:#{1,6}\s+|\*\*)?Fontes consultadas(?::)?(?:\*\*)?\s*$",
        content,
    )
    return content[: heading.start()].rstrip() if heading else content.strip()
