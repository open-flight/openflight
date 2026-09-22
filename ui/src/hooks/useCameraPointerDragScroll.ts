import { useEffect } from 'react';

export const CAMERA_DRAG_SCROLL_SELECTOR = '.camera-settings, .camera-feed__workspace';

const DRAG_THRESHOLD_PX = 6;
const INPUT_SELECTOR = "input, textarea, select, [contenteditable='true']";

type PointerStart = Pick<PointerEvent, 'button' | 'isPrimary' | 'pointerType'>;

export function shouldStartCameraPointerDrag(event: PointerStart): boolean {
  return event.pointerType === 'mouse' && event.isPrimary && event.button === 0;
}

export function dragScrollTop(startScrollTop: number, startY: number, currentY: number): number {
  return startScrollTop + startY - currentY;
}

function findScrollableRegion(target: EventTarget | null): HTMLElement | null {
  if (!(target instanceof Element) || target.closest(INPUT_SELECTOR)) return null;

  let region = target.closest<HTMLElement>(CAMERA_DRAG_SCROLL_SELECTOR);
  while (region && region.scrollHeight <= region.clientHeight + 1) {
    region = region.parentElement?.closest<HTMLElement>(CAMERA_DRAG_SCROLL_SELECTOR) ?? null;
  }
  return region;
}

/** Support Pi touchscreen drivers that expose finger drags as mouse pointers. */
export function useCameraPointerDragScroll(): void {
  useEffect(() => {
    let pointerId: number | null = null;
    let region: HTMLElement | null = null;
    let startX = 0;
    let startY = 0;
    let startScrollTop = 0;
    let dragging = false;

    const reset = () => {
      pointerId = null;
      region = null;
      dragging = false;
    };

    const onPointerDown = (event: PointerEvent) => {
      if (!shouldStartCameraPointerDrag(event)) return;
      const scrollRegion = findScrollableRegion(event.target);
      if (!scrollRegion) return;

      pointerId = event.pointerId;
      region = scrollRegion;
      startX = event.clientX;
      startY = event.clientY;
      startScrollTop = scrollRegion.scrollTop;
      dragging = false;
    };

    const onPointerMove = (event: PointerEvent) => {
      if (event.pointerId !== pointerId || !region) return;
      if (!dragging) {
        if (Math.hypot(event.clientX - startX, event.clientY - startY) < DRAG_THRESHOLD_PX) return;
        dragging = true;
      }
      event.preventDefault();
      region.scrollTop = dragScrollTop(startScrollTop, startY, event.clientY);
    };

    const onPointerEnd = (event: PointerEvent) => {
      if (event.pointerId === pointerId) reset();
    };

    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('pointermove', onPointerMove, { capture: true, passive: false });
    document.addEventListener('pointerup', onPointerEnd, true);
    document.addEventListener('pointercancel', onPointerEnd, true);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('pointermove', onPointerMove, true);
      document.removeEventListener('pointerup', onPointerEnd, true);
      document.removeEventListener('pointercancel', onPointerEnd, true);
    };
  }, []);
}
