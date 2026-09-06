"""tiling_for 불변식 검증: 빈틈/겹침 없음 + 면적이 우선순위 따라 단조 감소.

본 앱의 2행 6열과 시연 페이지의 2행 4열을 같은 불변식으로 함께 검증한다.
"""
import sys

sys.path.insert(0, ".")
from modules import playbook as pb

fails = []
for cols in (pb.GRID_COLS, pb.DEMO_GRID_COLS):
    total_cells = pb.GRID_ROWS * cols
    print(f"[{pb.GRID_ROWS}행 {cols}열 — {total_cells}칸]")
    for n in range(1, total_cells + 1):
        slots = pb.tiling_for(n, cols)
        assert len(slots) == n, f"cols={cols} n={n}: 슬롯 {len(slots)}개"

        # 1) 격자를 겹침 없이 정확히 덮는가
        cells = {}
        for i, (r, c, rs, cs) in enumerate(slots):
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    key = (rr, cc)
                    if key in cells:
                        fails.append(f"cols={cols} n={n}: {key} 중복 (패널 {cells[key]+1}, {i+1})")
                    if not (1 <= rr <= pb.GRID_ROWS and 1 <= cc <= cols):
                        fails.append(f"cols={cols} n={n}: {key} 격자 밖 (패널 {i+1})")
                    cells[key] = i
        if len(cells) != total_cells:
            fails.append(f"cols={cols} n={n}: {len(cells)}칸만 덮음 (빈칸 발생)")

        # 2) 면적이 우선순위를 따라 단조 감소하는가
        areas = [rs * cs for (_, _, rs, cs) in slots]
        for i in range(len(areas) - 1):
            if areas[i] < areas[i + 1]:
                fails.append(
                    f"cols={cols} n={n}: {i+1}순위({areas[i]}칸) < {i+2}순위({areas[i+1]}칸) 역전"
                )

        print(f"  n={n:2d} 면적={areas}")
    print()

# 3) retile은 화면 선택을 바꾸지 않고 좌표만 바꾼다
src = [{"source_id": f"S{i}", "name": f"화면{i}", "grid": (9, 9, 9, 9)} for i in range(1, 7)]
out = pb.retile(src, cols=pb.DEMO_GRID_COLS)
if [x["source_id"] for x in out] != [x["source_id"] for x in src]:
    fails.append("retile: 화면 선택이 바뀌었다")
if [x["grid"] for x in out] != pb.tiling_for(6, pb.DEMO_GRID_COLS):
    fails.append("retile: 좌표가 tiling_for와 다르다")
if [x["priority"] for x in out] != [1, 2, 3, 4, 5, 6]:
    fails.append("retile: priority가 순서와 다르다")
over = pb.retile([{"source_id": f"S{i}"} for i in range(20)], cols=pb.DEMO_GRID_COLS)
if len(over) != 8:
    fails.append(f"retile: 8칸을 넘겨 {len(over)}개를 냈다")
print(f"  retile 6개 -> {[x['grid'] for x in out]}")
print(f"  retile 20개 -> {len(over)}개로 잘림")

print()
if fails:
    print("실패:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("통과 — 2×6·2×4 모두 완전 덮기 + 면적 단조 감소, retile 불변식 유지")
