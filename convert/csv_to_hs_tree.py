import csv
import json

INPUT_CSV = "data.csv"
OUTPUT_JSON = "output.json"


def csv_to_tree(csv_path):
    stack = []
    roots = []

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    # Bỏ header
    if rows and rows[0][0].lower().replace("\ufeff", "") == "level":
        rows = rows[1:]

    for row in rows:
        if not row or not row[0].strip():
            continue

        level = int(row[0].strip())
        hs_code = row[1].strip() if len(row) > 1 and row[1].strip() else None
        vi_name = row[2].strip() if len(row) > 2 else ""
        en_name = row[3].strip() if len(row) > 3 else ""

        node = {
            "level": level,
            "hs_code": hs_code,
            "vi": vi_name.rstrip(":"),
            "en": en_name.rstrip(":"),
            "children": []
        }

        while stack and stack[-1]["level"] >= level:
            stack.pop()

        if stack:
            stack[-1]["children"].append(node)
        else:
            roots.append(node)

        stack.append(node)

    return roots


if __name__ == "__main__":
    tree = csv_to_tree(INPUT_CSV)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(tree, f, ensure_ascii=False, indent=2)

    print(f"✅ HS tree JSON saved to {OUTPUT_JSON}")
