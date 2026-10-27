"""Natural input motion for continuous recordings of the actual application.

The temporary cursor is a presentation overlay only. It does not replace application
content or synthesize results. Record the live browser at its normal video cadence.
"""

from __future__ import annotations

from typing import Any

CURSOR_SCRIPT = r"""
(() => {
  if (document.getElementById('demo-pointer')) return;
  const install = () => {
    if (document.getElementById('demo-pointer')) return;
    const style = document.createElement('style');
    style.textContent = [
      '*{cursor:none!important}',
      '#demo-pointer{position:fixed;top:0;left:0;width:22px;height:28px;pointer-events:none;z-index:2147483647;transform:translate3d(120px,76px,0);filter:drop-shadow(0 1px 2px #0005)}',
      '.demo-click{position:fixed;left:0;top:0;width:32px;height:32px;border:2px solid #55a586;border-radius:50%;pointer-events:none;z-index:2147483646;animation:demo-ripple .48s ease-out forwards}',
      '@keyframes demo-ripple{from{opacity:.8;transform:translate(-50%,-50%) scale(.25)}to{opacity:0;transform:translate(-50%,-50%) scale(1.5)}}'
    ].join('');
    document.head.appendChild(style);
    const pointer = document.createElement('div');
    pointer.id = 'demo-pointer';
    pointer.innerHTML = '<svg width="22" height="28" viewBox="0 0 22 28"><path d="M2 2L2 23L7.4 17.6L11.3 26L15.4 24L11.3 16L19 16Z" fill="#243b30" stroke="#ffffff" stroke-width="1.8" stroke-linejoin="round"/></svg>';
    document.body.appendChild(pointer);
    document.addEventListener('pointermove', event => {
      pointer.style.transform = 'translate3d(' + event.clientX + 'px,' + event.clientY + 'px,0)';
    }, {passive:true});
    document.addEventListener('pointerdown', event => {
      const ring = document.createElement('div');
      ring.className = 'demo-click';
      ring.style.left = event.clientX + 'px';
      ring.style.top = event.clientY + 'px';
      document.body.appendChild(ring);
      setTimeout(() => ring.remove(), 550);
    }, {passive:true});
  };
  if (document.body) install();
  else document.addEventListener('DOMContentLoaded', install, {once:true});
})();
"""


class Motion:
    def __init__(self, page: Any):
        self.page = page
        self.position = (120.0, 76.0)
        page.add_init_script(CURSOR_SCRIPT)

    def install(self) -> None:
        self.page.evaluate(CURSOR_SCRIPT)
        self.page.mouse.move(*self.position)

    def pause(self, milliseconds: int = 400) -> None:
        self.page.wait_for_timeout(milliseconds)

    hold = pause

    def show(self, locator: Any) -> None:
        locator.wait_for(state="visible")
        locator.evaluate(
            "(element) => { const r=element.getBoundingClientRect(); "
            "if(r.top < 92 || r.bottom > innerHeight-36) "
            "element.scrollIntoView({behavior:'smooth',block:'center'}); }"
        )
        self.pause(650)

    def scroll_to(self, y: float) -> None:
        self.page.evaluate("(top) => window.scrollTo({top,behavior:'smooth'})", y)
        self.pause(700)

    def move(self, locator: Any) -> None:
        self.show(locator)
        box = locator.bounding_box()
        if box is None:
            raise ValueError("The recorded control is not visible")
        target = (box["x"] + min(box["width"] / 2, 160), box["y"] + box["height"] / 2)
        start = self.position
        for step in range(1, 25):
            t = step / 24
            eased = t * t * (3 - 2 * t)
            self.page.mouse.move(
                start[0] + (target[0] - start[0]) * eased,
                start[1] + (target[1] - start[1]) * eased,
            )
            self.pause(13)
        self.position = target
        self.pause(100)

    def click(self, locator: Any) -> None:
        self.move(locator)
        self.page.mouse.down()
        self.pause(70)
        self.page.mouse.up()
        self.pause(240)

    def type(self, locator: Any, text: str, delay: int = 28) -> None:
        self.click(locator)
        locator.press("ControlOrMeta+A")
        locator.press_sequentially(text, delay=delay)
        self.pause(280)
