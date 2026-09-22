import { describe, expect, it, vi } from 'vitest';
import {
  applyDragScrollMove,
  createDragScrollController,
  DRAG_SCROLL_THRESHOLD_PX,
  isEditableDragScrollTarget,
  startDragScroll,
} from './dragScroll';

describe('drag-scroll math', () => {
  it('does not move until the pointer travels past the tap threshold', () => {
    const session = startDragScroll(1, 100, 40);
    const next = applyDragScrollMove(session, 100 + DRAG_SCROLL_THRESHOLD_PX - 1);

    expect(next.moved).toBe(false);
    expect(next.scrollTop).toBe(40);
    expect(session.moved).toBe(false);
  });

  it('scrolls in the opposite direction of the drag once the threshold is crossed', () => {
    const session = startDragScroll(1, 100, 40);
    const next = applyDragScrollMove(session, 40);

    expect(next.moved).toBe(true);
    expect(next.scrollTop).toBe(100);
    expect(session.moved).toBe(true);
  });

  it('keeps scrolling after the first move without re-checking the threshold', () => {
    const session = startDragScroll(1, 100, 0);
    applyDragScrollMove(session, 100 - DRAG_SCROLL_THRESHOLD_PX);
    const next = applyDragScrollMove(session, 99);

    expect(next.moved).toBe(true);
    expect(next.scrollTop).toBe(1);
  });
});

describe('isEditableDragScrollTarget', () => {
  it('ignores form controls so validation fields stay usable', () => {
    expect(isEditableDragScrollTarget({ nodeName: 'INPUT' })).toBe(true);
    expect(isEditableDragScrollTarget({ nodeName: 'select' })).toBe(true);
    expect(isEditableDragScrollTarget({ nodeName: 'TEXTAREA' })).toBe(true);
    expect(isEditableDragScrollTarget({ nodeName: 'OPTION' })).toBe(true);
  });

  it('allows dragging from shot row buttons', () => {
    expect(isEditableDragScrollTarget({ nodeName: 'BUTTON' })).toBe(false);
    expect(isEditableDragScrollTarget({ nodeName: 'SPAN' })).toBe(false);
    expect(isEditableDragScrollTarget(null)).toBe(false);
  });

  it('uses closest() when the event target is nested inside a field', () => {
    expect(isEditableDragScrollTarget({ nodeName: 'SPAN', closest: () => ({}) })).toBe(true);
    expect(isEditableDragScrollTarget({ nodeName: 'SPAN', closest: () => null })).toBe(false);
  });
});

describe('createDragScrollController', () => {
  function fakeElement({ scrollTop = 0, scrollLeft = 0 } = {}) {
    return {
      scrollTop,
      scrollLeft,
      setPointerCapture: vi.fn(),
      releasePointerCapture: vi.fn(),
      hasPointerCapture: vi.fn(() => true),
    };
  }

  it('drags the list and swallows the trailing click so a row is not tapped', () => {
    const element = fakeElement({ scrollTop: 12 });
    const controller = createDragScrollController(() => element);

    controller.pointerDown({ button: 0, pointerId: 7, clientY: 200, target: { nodeName: 'BUTTON' } });
    controller.pointerMove({ button: 0, pointerId: 7, clientY: 120, target: { nodeName: 'BUTTON' } });

    expect(element.scrollTop).toBe(92);
    expect(element.setPointerCapture).toHaveBeenCalledWith(7);

    controller.pointerUp({ pointerId: 7 });
    const click = { preventDefault: vi.fn(), stopPropagation: vi.fn() };

    expect(controller.clickCapture(click)).toBe(true);
    expect(click.preventDefault).toHaveBeenCalled();
    expect(click.stopPropagation).toHaveBeenCalled();
    expect(element.releasePointerCapture).toHaveBeenCalledWith(7);
    expect(controller.clickCapture(click)).toBe(false);
  });

  it('leaves a tap under the threshold as a click', () => {
    const element = fakeElement();
    const controller = createDragScrollController(() => element);

    controller.pointerDown({ button: 0, pointerId: 1, clientY: 50, target: { nodeName: 'BUTTON' } });
    controller.pointerMove({
      button: 0,
      pointerId: 1,
      clientY: 50 + DRAG_SCROLL_THRESHOLD_PX - 1,
      target: { nodeName: 'BUTTON' },
    });
    controller.pointerUp({ pointerId: 1 });

    expect(element.scrollTop).toBe(0);
    expect(controller.clickCapture({ preventDefault: vi.fn(), stopPropagation: vi.fn() })).toBe(false);
  });

  it('does not capture the pointer on finger-down, so a row tap still clicks', () => {
    const element = fakeElement();
    const controller = createDragScrollController(() => element);

    controller.pointerDown({ button: 0, pointerId: 1, clientY: 50, target: { nodeName: 'BUTTON' } });

    expect(element.setPointerCapture).not.toHaveBeenCalled();

    controller.pointerUp({ pointerId: 1 });
    expect(controller.clickCapture({ preventDefault: vi.fn(), stopPropagation: vi.fn() })).toBe(false);
  });

  it('treats typical touchscreen jitter as a tap, not a drag', () => {
    const element = fakeElement();
    const controller = createDragScrollController(() => element);

    controller.pointerDown({ button: 0, pointerId: 1, clientY: 50, target: { nodeName: 'BUTTON' } });
    controller.pointerMove({ button: 0, pointerId: 1, clientY: 62, target: { nodeName: 'BUTTON' } });
    controller.pointerUp({ pointerId: 1 });

    expect(element.scrollTop).toBe(0);
    expect(element.setPointerCapture).not.toHaveBeenCalled();
    expect(controller.clickCapture({ preventDefault: vi.fn(), stopPropagation: vi.fn() })).toBe(false);
  });

  it('does not start a drag from an editable field', () => {
    const element = fakeElement();
    const controller = createDragScrollController(() => element);

    controller.pointerDown({ button: 0, pointerId: 1, clientY: 80, target: { nodeName: 'INPUT' } });
    controller.pointerMove({ button: 0, pointerId: 1, clientY: 10, target: { nodeName: 'INPUT' } });

    expect(element.scrollTop).toBe(0);
    expect(element.setPointerCapture).not.toHaveBeenCalled();
  });

  it('ignores non-primary mouse buttons', () => {
    const element = fakeElement();
    const controller = createDragScrollController(() => element);

    controller.pointerDown({ button: 2, pointerId: 1, clientY: 80, target: { nodeName: 'BUTTON' } });
    controller.pointerMove({ button: 2, pointerId: 1, clientY: 10, target: { nodeName: 'BUTTON' } });

    expect(element.scrollTop).toBe(0);
  });

  it('drags horizontally and swallows the trailing click so a chip is not selected', () => {
    const element = fakeElement({ scrollLeft: 12 });
    const controller = createDragScrollController(() => element, { axis: 'x' });

    controller.pointerDown({
      button: 0,
      pointerId: 7,
      clientX: 200,
      clientY: 10,
      target: { nodeName: 'BUTTON' },
    });
    controller.pointerMove({
      button: 0,
      pointerId: 7,
      clientX: 120,
      clientY: 10,
      target: { nodeName: 'BUTTON' },
    });

    expect(element.scrollLeft).toBe(92);
    expect(element.scrollTop).toBe(0);
    expect(element.setPointerCapture).toHaveBeenCalledWith(7);

    controller.pointerUp({ pointerId: 7 });
    const click = { preventDefault: vi.fn(), stopPropagation: vi.fn() };

    expect(controller.clickCapture(click)).toBe(true);
    expect(click.preventDefault).toHaveBeenCalled();
    expect(click.stopPropagation).toHaveBeenCalled();
  });

  it('leaves a horizontal tap under the threshold as a click', () => {
    const element = fakeElement();
    const controller = createDragScrollController(() => element, { axis: 'x' });

    controller.pointerDown({
      button: 0,
      pointerId: 1,
      clientX: 50,
      clientY: 10,
      target: { nodeName: 'BUTTON' },
    });
    controller.pointerMove({
      button: 0,
      pointerId: 1,
      clientX: 50 + DRAG_SCROLL_THRESHOLD_PX - 1,
      clientY: 10,
      target: { nodeName: 'BUTTON' },
    });
    controller.pointerUp({ pointerId: 1 });

    expect(element.scrollLeft).toBe(0);
    expect(controller.clickCapture({ preventDefault: vi.fn(), stopPropagation: vi.fn() })).toBe(false);
  });
});
