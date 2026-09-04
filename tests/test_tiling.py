"""tiling_for 불변식 검증: 빈틈/겹침 없음 + 면적이 우선순위 따라 단조 감소."""
import sys
sys.path.insert(0, ".")
from modules import playbook as pb

fails = []
for n in range(1, pb.MAX_PANELS + 1):
    slots = pb.tiling_for(n)
    assert len(slots) == n, f"n={n}: 슬롯 {len(slots)}개"

    # 1) 12칸을 겹침 없이 정확히 덮는가
    cells = {}
    for i, (r, c, rs, cs) in enumerate(slots):
        for rr in range(r, r + rs):
            for cc in range(c, c + cs):
                key = (rr, cc)
                if key in cells:
                    fails.append(f"n={n}: {key} 중복 (패널 {cells[key]+1}, {i+1})")
                if not (1 <= rr <= pb.GRID_ROWS and 1 <= cc <= pb.GRID_COLS):
                    fails.append(f"n={n}: {key} 격자 밖 (패널 {i+1})")
                cells[key] = i
    if len(cells) != pb.GRID_ROWS * pb.GRID_COLS:
        fails.append(f"n={n}: {len(cells)}칸만 덮음 (빈칸 발생)")

    # 2) 면적이 우선순위를 따라 단조 감소하는가
    areas = [rs * cs for (_, _, rs, cs) in slots]
    for i in range(len(areas) - 1):
        if areas[i] < areas[i + 1]:
            fails.append(f"n={n}: {i+1}순위({areas[i]}칸) < {i+2}순위({areas[i+1]}칸) 역전")

    print(f"  n={n:2d} 면적={areas}")

print()
if fails:
    print("실패:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("통과 — 전 n에서 12칸 완전 덮기 + 우선순위별 면적 단조 감소")
