import { describe, expect, it } from 'vitest';
import { CAMERA_DRAG_SCROLL_SELECTOR, dragScrollTop, shouldStartCameraPointerDrag } from './useCameraPointerDragScroll';

describe('camera pointer drag scrolling', () => {
  it('moves the settings panel opposite the pointer drag', () => {
    expect(dragScrollTop(120, 100, 140)).toBe(80);
    expect(dragScrollTop(120, 100, 60)).toBe(160);
  });

  it('handles the mouse-emulated primary pointer used by the kiosk touchscreen', () => {
    expect(shouldStartCameraPointerDrag({ button: 0, isPrimary: true, pointerType: 'mouse' })).toBe(true);
    expect(shouldStartCameraPointerDrag({ button: 0, isPrimary: true, pointerType: 'touch' })).toBe(false);
    expect(shouldStartCameraPointerDrag({ button: 1, isPrimary: true, pointerType: 'mouse' })).toBe(false);
  });

  it('is scoped to camera regions', () => {
    expect(CAMERA_DRAG_SCROLL_SELECTOR).toContain('.camera-settings');
    expect(CAMERA_DRAG_SCROLL_SELECTOR).toContain('.camera-feed__workspace');
    expect(CAMERA_DRAG_SCROLL_SELECTOR).not.toContain('.shot-list__rows');
  });
});
