/** Movement past this many pixels counts as a scroll, not a tap.
 *  Touchscreens jitter by ~8–12px on a stationary press; Chromium's
 *  default touch slop is 15px. */
export const DRAG_SCROLL_THRESHOLD_PX = 16;

const EDITABLE_TAGS = new Set(['INPUT', 'SELECT', 'TEXTAREA', 'OPTION']);

export interface DragScrollSession {
  pointerId: number;
  startY: number;
  startScrollTop: number;
  moved: boolean;
}

export interface DragScrollTarget {
  nodeName?: string;
  closest?: (selector: string) => unknown;
}

export type DragScrollAxis = 'x' | 'y';

export interface DragScrollOptions {
  axis?: DragScrollAxis;
}

export interface DragScrollElement {
  scrollTop: number;
  scrollLeft: number;
  setPointerCapture?: (pointerId: number) => void;
  releasePointerCapture?: (pointerId: number) => void;
  hasPointerCapture?: (pointerId: number) => boolean;
}

export interface DragScrollPointerEvent {
  button: number;
  pointerId: number;
  clientX?: number;
  clientY: number;
  target: DragScrollTarget | null;
}

export interface DragScrollClickEvent {
  preventDefault: () => void;
  stopPropagation: () => void;
}

export function isEditableDragScrollTarget(target: DragScrollTarget | null): boolean {
  if (!target) return false;
  if (typeof target.closest === 'function') {
    return Boolean(target.closest('input, select, textarea, option'));
  }
  return EDITABLE_TAGS.has((target.nodeName ?? '').toUpperCase());
}

export function startDragScroll(pointerId: number, clientY: number, scrollTop: number): DragScrollSession {
  return { pointerId, startY: clientY, startScrollTop: scrollTop, moved: false };
}

export function applyDragScrollMove(
  session: DragScrollSession,
  clientY: number,
  thresholdPx = DRAG_SCROLL_THRESHOLD_PX
): { scrollTop: number; moved: boolean } {
  const deltaY = clientY - session.startY;
  if (!session.moved && Math.abs(deltaY) < thresholdPx) {
    return { scrollTop: session.startScrollTop, moved: false };
  }

  session.moved = true;
  return { scrollTop: session.startScrollTop - deltaY, moved: true };
}

/**
 * Pointer drag-to-scroll for kiosk touchscreens. Many Pi displays report
 * finger motion as mouse drags, which native overflow scrolling ignores.
 */
export function createDragScrollController(
  getElement: () => DragScrollElement | null,
  options: DragScrollOptions = {}
) {
  const axis: DragScrollAxis = options.axis ?? 'y';
  let session: DragScrollSession | null = null;
  let suppressClick = false;

  const clientPos = (event: DragScrollPointerEvent) => (axis === 'x' ? (event.clientX ?? 0) : event.clientY);

  const readScroll = (element: DragScrollElement) => (axis === 'x' ? element.scrollLeft : element.scrollTop);

  const writeScroll = (element: DragScrollElement, value: number) => {
    if (axis === 'x') {
      element.scrollLeft = value;
    } else {
      element.scrollTop = value;
    }
  };

  const endSession = (pointerId: number) => {
    const current = session;
    if (!current || current.pointerId !== pointerId) return;

    suppressClick = current.moved;
    session = null;

    const element = getElement();
    try {
      if (element?.hasPointerCapture?.(pointerId)) {
        element.releasePointerCapture?.(pointerId);
      }
    } catch {
      // Capture was already released.
    }
  };

  return {
    pointerDown(event: DragScrollPointerEvent) {
      if (event.button !== 0) return;
      if (isEditableDragScrollTarget(event.target)) return;

      const element = getElement();
      if (!element) return;

      session = startDragScroll(event.pointerId, clientPos(event), readScroll(element));
      suppressClick = false;
    },

    pointerMove(event: DragScrollPointerEvent) {
      if (!session || session.pointerId !== event.pointerId) return;

      const element = getElement();
      if (!element) return;

      const next = applyDragScrollMove(session, clientPos(event));
      if (!next.moved) return;

      writeScroll(element, next.scrollTop);
      // Capture only after the gesture is a drag. Capturing on finger-down
      // retargets the click to the list, so row taps never open details.
      try {
        element.setPointerCapture?.(event.pointerId);
      } catch {
        // Pointer already released, or the node is not in the tree.
      }
    },

    pointerUp(event: Pick<DragScrollPointerEvent, 'pointerId'>) {
      endSession(event.pointerId);
    },

    pointerCancel(event: Pick<DragScrollPointerEvent, 'pointerId'>) {
      endSession(event.pointerId);
    },

    clickCapture(event: DragScrollClickEvent) {
      if (!suppressClick) return false;
      event.preventDefault();
      event.stopPropagation();
      suppressClick = false;
      return true;
    },
  };
}
