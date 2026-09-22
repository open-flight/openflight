import { type MouseEvent, type PointerEvent, type RefObject, useRef } from 'react';
import { createDragScrollController, type DragScrollAxis, type DragScrollTarget } from '../utils/dragScroll';

/**
 * Bind pointer drag-to-scroll onto an overflow container for kiosk touchscreens.
 * Pass a ref created in the component (`useRef`) so the scroller can use
 * `ref={scrollRef}` — returning a ref from this hook trips react-hooks/refs.
 */
export function useDragScroll<T extends HTMLElement>(ref: RefObject<T | null>, axis: DragScrollAxis = 'y') {
  const controllerRef = useRef<ReturnType<typeof createDragScrollController> | null>(null);

  const getController = () => {
    controllerRef.current ??= createDragScrollController(() => ref.current, { axis });
    return controllerRef.current;
  };

  return {
    onPointerDown: (event: PointerEvent<T>) =>
      getController().pointerDown({
        button: event.button,
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        target: event.target as DragScrollTarget | null,
      }),
    onPointerMove: (event: PointerEvent<T>) =>
      getController().pointerMove({
        button: event.button,
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        target: event.target as DragScrollTarget | null,
      }),
    onPointerUp: (event: PointerEvent<T>) => getController().pointerUp(event),
    onPointerCancel: (event: PointerEvent<T>) => getController().pointerCancel(event),
    onClickCapture: (event: MouseEvent<T>) => getController().clickCapture(event),
  };
}
