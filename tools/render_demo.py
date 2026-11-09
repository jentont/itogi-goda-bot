import sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import cards
from tools.demo_data import demo
out = pathlib.Path(sys.argv[1])
d = demo()
for kind in ("card", "story"):
    t = time.time(); p = cards.render(d, kind, out / f"demo_{kind}.mp4"); print(kind, round(time.time()-t, 2), "s", p.stat().st_size//1024, "KB")
t = time.time(); cards.render(None, "card", out / "generic_card.mp4"); print("generic", round(time.time()-t,2))
