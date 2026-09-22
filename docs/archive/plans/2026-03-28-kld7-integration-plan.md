# K-LD7 Full Stack Integration — Implementation Plan

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the K-LD7 angle radar into the openflight server so it provides real-time vertical (or horizontal) angle data alongside OPS243 speed/spin data, displayed in the UI.

**Architecture:** K-LD7 streams TDAT+PDAT frames in a background thread into a ring buffer. When OPS243 detects a shot, we search the ring buffer for the ball pass (highest-magnitude event) and attach the angle to the Shot. The UI shows separate Vertical and Horizontal angle cards.

**Tech Stack:** Python (kld7 library, pyserial), React/TypeScript, Flask-SocketIO

**Spec:** `docs/plans/2026-03-28-kld7-integration-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/openflight/kld7/__init__.py` | Create | Package exports |
| `src/openflight/kld7/types.py` | Create | KLD7Angle, KLD7Frame dataclasses |
| `src/openflight/kld7/tracker.py` | Create | KLD7Tracker: connection, ring buffer, angle extraction |
| `src/openflight/server.py` | Modify | CLI flags, init_kld7(), on_shot_detected() K-LD7 step |
| `scripts/start-kiosk.sh` | Modify | Pass --kld7 flags |
| `ui/src/types/shot.ts` | Modify | Add angle_source field |
| `ui/src/components/ShotDisplay.tsx` | Modify | Split Launch Angle into Vertical + Horizontal cards |
| `tests/test_kld7.py` | Create | Unit tests for KLD7Tracker |
| `tests/test_server.py` | Modify | Test shot_to_dict with K-LD7 angle data |

---

### Task 1: K-LD7 Data Types

**Files:**
- Create: `src/openflight/kld7/__init__.py`
- Create: `src/openflight/kld7/types.py`
- Test: `tests/test_kld7.py`

- [ ] **Step 1: Create the kld7 package with types**

```bash
mkdir -p src/openflight/kld7
```

- [ ] **Step 2: Write types.py**

Create `src/openflight/kld7/types.py`:

```python
"""Data types for K-LD7 angle radar integration."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class KLD7Frame:
    """A single frame from the K-LD7 radar stream."""

    timestamp: float
    tdat: Optional[dict] = None  # {"distance", "speed", "angle", "magnitude"}
    pdat: list = field(default_factory=list)  # list of target dicts


@dataclass
class KLD7Angle:
    """Angle measurement extracted from K-LD7 ring buffer after a shot."""

    vertical_deg: Optional[float] = None  # Populated when orientation="vertical"
    horizontal_deg: Optional[float] = None  # Populated when orientation="horizontal"
    distance_m: float = 0.0
    magnitude: float = 0.0
    confidence: float = 0.0  # 0.0 - 1.0
    num_frames: int = 0  # Frames with detection in the event
```

- [ ] **Step 3: Write __init__.py**

Create `src/openflight/kld7/__init__.py`:

```python
"""K-LD7 angle radar integration module."""

from .types import KLD7Angle, KLD7Frame

__all__ = ["KLD7Angle", "KLD7Frame"]
```

- [ ] **Step 4: Write basic type tests**

Create `tests/test_kld7.py`:

```python
"""Tests for K-LD7 angle radar integration."""

import pytest
from openflight.kld7.types import KLD7Angle, KLD7Frame


class TestKLD7Types:
    """Tests for K-LD7 data types."""

    def test_kld7_frame_defaults(self):
        frame = KLD7Frame(timestamp=1000.0)
        assert frame.timestamp == 1000.0
        assert frame.tdat is None
        assert frame.pdat == []

    def test_kld7_angle_vertical(self):
        angle = KLD7Angle(vertical_deg=12.5, distance_m=2.0, magnitude=5000, confidence=0.8, num_frames=3)
        assert angle.vertical_deg == 12.5
        assert angle.horizontal_deg is None

    def test_kld7_angle_horizontal(self):
        angle = KLD7Angle(horizontal_deg=-3.2, distance_m=1.5, magnitude=4000, confidence=0.7, num_frames=2)
        assert angle.horizontal_deg == -3.2
        assert angle.vertical_deg is None
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_kld7.py -v`
Expected: 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/openflight/kld7/ tests/test_kld7.py
git commit -m "feat(kld7): add K-LD7 data types (KLD7Angle, KLD7Frame)"
```

---

### Task 2: KLD7Tracker — Ring Buffer and Angle Extraction

**Files:**
- Create: `src/openflight/kld7/tracker.py`
- Modify: `src/openflight/kld7/__init__.py`
- Test: `tests/test_kld7.py`

- [ ] **Step 1: Write failing tests for KLD7Tracker**

Add to `tests/test_kld7.py`:

```python
import time
from unittest.mock import MagicMock, patch
from openflight.kld7.tracker import KLD7Tracker
from openflight.kld7.types import KLD7Angle, KLD7Frame


class TestKLD7TrackerRingBuffer:
    """Tests for ring buffer and angle extraction logic (no hardware)."""

    def _make_tracker(self, orientation="vertical"):
        """Create a tracker without connecting to hardware."""
        tracker = KLD7Tracker.__new__(KLD7Tracker)
        tracker.orientation = orientation
        tracker.buffer_seconds = 2.0
        tracker.max_buffer_frames = 70
        tracker._init_ring_buffer()
        return tracker

    def test_ring_buffer_stores_frames(self):
        tracker = self._make_tracker()
        now = time.time()
        for i in range(5):
            tracker._add_frame(KLD7Frame(
                timestamp=now + i * 0.03,
                tdat={"distance": 1.0, "speed": 5.0, "angle": 10.0 + i, "magnitude": 3000 + i * 100},
                pdat=[],
            ))
        assert len(tracker._ring_buffer) == 5

    def test_ring_buffer_max_size(self):
        tracker = self._make_tracker()
        tracker.max_buffer_frames = 10
        now = time.time()
        for i in range(20):
            tracker._add_frame(KLD7Frame(
                timestamp=now + i * 0.03,
                tdat={"distance": 1.0, "speed": 5.0, "angle": 0.0, "magnitude": 1000},
                pdat=[],
            ))
        assert len(tracker._ring_buffer) == 10

    def test_get_angle_finds_highest_magnitude_event(self):
        tracker = self._make_tracker(orientation="vertical")
        now = time.time()
        # Background noise frames
        for i in range(10):
            tracker._add_frame(KLD7Frame(
                timestamp=now + i * 0.03,
                tdat=None,
                pdat=[],
            ))
        # Ball pass: 3 frames with high magnitude at angle ~15°
        for i in range(3):
            tracker._add_frame(KLD7Frame(
                timestamp=now + 0.30 + i * 0.03,
                tdat={"distance": 2.0, "speed": 50.0, "angle": 14.0 + i, "magnitude": 5000 + i * 100},
                pdat=[{"distance": 2.0, "speed": 50.0, "angle": 14.0 + i, "magnitude": 5000 + i * 100}],
            ))
        # More noise after
        for i in range(5):
            tracker._add_frame(KLD7Frame(
                timestamp=now + 0.50 + i * 0.03,
                tdat=None,
                pdat=[],
            ))

        result = tracker.get_angle_for_shot()
        assert result is not None
        assert result.vertical_deg is not None
        assert 13.0 < result.vertical_deg < 17.0
        assert result.horizontal_deg is None
        assert result.num_frames == 3
        assert result.confidence > 0.0
        assert result.distance_m > 0.0

    def test_get_angle_returns_none_when_no_detections(self):
        tracker = self._make_tracker()
        now = time.time()
        for i in range(5):
            tracker._add_frame(KLD7Frame(timestamp=now + i * 0.03, tdat=None, pdat=[]))
        result = tracker.get_angle_for_shot()
        assert result is None

    def test_get_angle_horizontal_orientation(self):
        tracker = self._make_tracker(orientation="horizontal")
        now = time.time()
        tracker._add_frame(KLD7Frame(
            timestamp=now,
            tdat={"distance": 1.5, "speed": 30.0, "angle": -5.0, "magnitude": 4500},
            pdat=[{"distance": 1.5, "speed": 30.0, "angle": -5.0, "magnitude": 4500}],
        ))
        result = tracker.get_angle_for_shot()
        assert result is not None
        assert result.horizontal_deg is not None
        assert result.vertical_deg is None

    def test_reset_clears_buffer(self):
        tracker = self._make_tracker()
        tracker._add_frame(KLD7Frame(timestamp=time.time(), tdat={"distance": 1.0, "speed": 5.0, "angle": 0.0, "magnitude": 3000}, pdat=[]))
        assert len(tracker._ring_buffer) == 1
        tracker.reset()
        assert len(tracker._ring_buffer) == 0

    def test_prefers_pdat_over_tdat(self):
        """PDAT raw detections should be preferred for angle extraction."""
        tracker = self._make_tracker(orientation="vertical")
        now = time.time()
        # Frame with TDAT at 10° but PDAT at 20° (higher magnitude)
        tracker._add_frame(KLD7Frame(
            timestamp=now,
            tdat={"distance": 1.0, "speed": 5.0, "angle": 10.0, "magnitude": 3000},
            pdat=[{"distance": 1.5, "speed": 8.0, "angle": 20.0, "magnitude": 5000}],
        ))
        result = tracker.get_angle_for_shot()
        assert result is not None
        # Should use the PDAT target with higher magnitude
        assert abs(result.vertical_deg - 20.0) < 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_kld7.py::TestKLD7TrackerRingBuffer -v`
Expected: FAIL — `tracker` module doesn't exist yet

- [ ] **Step 3: Implement KLD7Tracker**

Create `src/openflight/kld7/tracker.py`:

```python
"""K-LD7 angle radar tracker with ring buffer for shot correlation."""

import logging
import threading
import time
from collections import deque
from typing import Optional

from .types import KLD7Angle, KLD7Frame

logger = logging.getLogger(__name__)


def _target_to_dict(target):
    """Convert a kld7 Target namedtuple to a dict."""
    if target is None:
        return None
    return {
        "distance": target.distance,
        "speed": target.speed,
        "angle": target.angle,
        "magnitude": target.magnitude,
    }


def _find_port():
    """Auto-detect K-LD7 EVAL board USB serial port."""
    try:
        from serial.tools.list_ports import comports
    except ImportError:
        return None
    for port in comports():
        desc = (port.description or "").lower()
        mfg = (port.manufacturer or "").lower()
        if any(kw in desc for kw in ["ftdi", "cp210", "usb-serial", "uart"]):
            return port.device
        if any(kw in mfg for kw in ["ftdi", "silicon labs"]):
            return port.device
    return None


class KLD7Tracker:
    """
    K-LD7 angle radar tracker.

    Streams TDAT+PDAT frames in a background thread into a ring buffer.
    When the OPS243 detects a shot, call get_angle_for_shot() to search
    the buffer for the ball pass and extract angle data.
    """

    def __init__(
        self,
        port: Optional[str] = None,
        range_m: int = 5,
        speed_kmh: int = 100,
        orientation: str = "vertical",
        buffer_seconds: float = 2.0,
    ):
        self.port = port
        self.range_m = range_m
        self.speed_kmh = speed_kmh
        self.orientation = orientation
        self.buffer_seconds = buffer_seconds
        # ~34 fps without FFT * 2 seconds
        self.max_buffer_frames = int(34 * buffer_seconds)

        self._radar = None
        self._stream_thread: Optional[threading.Thread] = None
        self._running = False
        self._init_ring_buffer()

    def _init_ring_buffer(self):
        """Initialize or reset the ring buffer."""
        self._ring_buffer: deque[KLD7Frame] = deque(maxlen=self.max_buffer_frames)

    def connect(self) -> bool:
        """Connect to K-LD7 and configure for golf."""
        try:
            from kld7 import KLD7, KLD7Exception
        except ImportError:
            logger.error("kld7 package not installed. Run: pip install kld7")
            return False

        port = self.port or _find_port()
        if not port:
            logger.error("No K-LD7 EVAL board detected")
            return False

        try:
            self._radar = KLD7(port, baudrate=115200)
            logger.info("K-LD7 connected on %s", port)
        except Exception as e:
            logger.error("K-LD7 connection failed: %s", e)
            return False

        # Configure for golf
        self._configure_for_golf()
        return True

    def _configure_for_golf(self):
        """Configure K-LD7 parameters for golf ball detection."""
        range_settings = {5: 0, 10: 1, 30: 2, 100: 3}
        speed_settings = {12: 0, 25: 1, 50: 2, 100: 3}

        params = self._radar.params
        params.RRAI = range_settings.get(self.range_m, 0)
        params.RSPI = speed_settings.get(self.speed_kmh, 3)
        params.DEDI = 2  # Both directions
        params.THOF = 10  # Low threshold
        params.TRFT = 1  # Fast detection
        params.MIAN = -90
        params.MAAN = 90
        params.MIRA = 0
        params.MARA = 100
        params.MISP = 0
        params.MASP = 100
        params.VISU = 0  # No vibration suppression

        logger.info(
            "K-LD7 configured: range=%dm, speed=%dkm/h, orientation=%s",
            self.range_m, self.speed_kmh, self.orientation,
        )

    def start(self):
        """Start the background streaming thread."""
        if self._running:
            return
        self._running = True
        self._stream_thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._stream_thread.start()
        logger.info("K-LD7 streaming started (orientation=%s)", self.orientation)

    def stop(self):
        """Stop streaming and close connection."""
        self._running = False
        if self._stream_thread:
            self._stream_thread.join(timeout=5)
            self._stream_thread = None
        if self._radar:
            try:
                self._radar.close()
            except Exception:
                pass
            try:
                self._radar._port = None
            except Exception:
                pass
            self._radar = None
        logger.info("K-LD7 stopped")

    def _stream_loop(self):
        """Background thread: stream TDAT+PDAT into ring buffer."""
        from kld7 import FrameCode

        frame_codes = FrameCode.TDAT | FrameCode.PDAT
        current_frame = KLD7Frame(timestamp=time.time())
        seen_in_frame = set()

        try:
            for code, payload in self._radar.stream_frames(frame_codes, max_count=-1):
                if not self._running:
                    break

                # Detect frame boundary
                if code in seen_in_frame:
                    self._add_frame(current_frame)
                    current_frame = KLD7Frame(timestamp=time.time())
                    seen_in_frame = set()

                seen_in_frame.add(code)

                if code == "TDAT":
                    current_frame.tdat = _target_to_dict(payload)
                elif code == "PDAT":
                    current_frame.pdat = [_target_to_dict(t) for t in payload] if payload else []

            # Save final frame
            if seen_in_frame:
                self._add_frame(current_frame)

        except Exception as e:
            if self._running:
                logger.error("K-LD7 stream error: %s", e)

    def _add_frame(self, frame: KLD7Frame):
        """Add a frame to the ring buffer."""
        self._ring_buffer.append(frame)

    def get_angle_for_shot(self) -> Optional[KLD7Angle]:
        """
        Search the ring buffer for the ball pass and extract angle data.

        Finds the highest-magnitude detection event in the buffer.
        Prefers PDAT (raw detections) over TDAT (tracked target).
        Returns KLD7Angle or None if no detections found.
        """
        # Collect all detections from the buffer
        detections = []  # (timestamp, angle, distance, magnitude, source)

        for frame in self._ring_buffer:
            # Prefer PDAT targets (more reliable for transient targets)
            if frame.pdat:
                for target in frame.pdat:
                    if target is not None and target.get("magnitude", 0) > 0:
                        detections.append((
                            frame.timestamp,
                            target["angle"],
                            target["distance"],
                            target["magnitude"],
                        ))
            elif frame.tdat and frame.tdat.get("magnitude", 0) > 0:
                detections.append((
                    frame.timestamp,
                    frame.tdat["angle"],
                    frame.tdat["distance"],
                    frame.tdat["magnitude"],
                ))

        if not detections:
            return None

        # Find the peak magnitude detection
        peak = max(detections, key=lambda d: d[3])
        peak_time = peak[0]

        # Collect all detections within 0.5s of the peak (the "event")
        event_detections = [
            d for d in detections if abs(d[0] - peak_time) < 0.5
        ]

        if not event_detections:
            return None

        # Weighted average angle (weighted by magnitude)
        total_mag = sum(d[3] for d in event_detections)
        if total_mag == 0:
            return None

        avg_angle = sum(d[1] * d[3] for d in event_detections) / total_mag
        avg_distance = sum(d[2] * d[3] for d in event_detections) / total_mag
        max_magnitude = max(d[3] for d in event_detections)
        num_frames = len(set(d[0] for d in event_detections))

        # Confidence based on:
        # - Number of frames with detections (more = better)
        # - Magnitude strength (higher = better signal)
        # - Angle consistency (lower std dev = better)
        frame_score = min(num_frames / 3.0, 1.0)  # 3+ frames = full score
        mag_score = min(max_magnitude / 5000.0, 1.0)  # 5000+ = full score

        angles = [d[1] for d in event_detections]
        if len(angles) > 1:
            mean_angle = sum(angles) / len(angles)
            angle_std = (sum((a - mean_angle) ** 2 for a in angles) / len(angles)) ** 0.5
            consistency_score = max(0.0, 1.0 - angle_std / 20.0)  # 20°+ std = 0
        else:
            consistency_score = 0.5  # Single frame — moderate confidence

        confidence = frame_score * 0.4 + mag_score * 0.3 + consistency_score * 0.3
        confidence = round(min(max(confidence, 0.0), 1.0), 2)

        # Map angle to orientation
        if self.orientation == "vertical":
            return KLD7Angle(
                vertical_deg=round(avg_angle, 1),
                horizontal_deg=None,
                distance_m=round(avg_distance, 2),
                magnitude=max_magnitude,
                confidence=confidence,
                num_frames=num_frames,
            )
        else:
            return KLD7Angle(
                vertical_deg=None,
                horizontal_deg=round(avg_angle, 1),
                distance_m=round(avg_distance, 2),
                magnitude=max_magnitude,
                confidence=confidence,
                num_frames=num_frames,
            )

    def reset(self):
        """Clear the ring buffer after a shot is processed."""
        self._ring_buffer.clear()
```

- [ ] **Step 4: Update __init__.py exports**

Modify `src/openflight/kld7/__init__.py`:

```python
"""K-LD7 angle radar integration module."""

from .types import KLD7Angle, KLD7Frame
from .tracker import KLD7Tracker

__all__ = ["KLD7Angle", "KLD7Frame", "KLD7Tracker"]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_kld7.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/openflight/kld7/ tests/test_kld7.py
git commit -m "feat(kld7): implement KLD7Tracker with ring buffer and angle extraction"
```

---

### Task 3: Server Integration — CLI Flags and Init

**Files:**
- Modify: `src/openflight/server.py:1169-1277` (argparse), `src/openflight/server.py:1294-1359` (main startup)
- Modify: `scripts/start-kiosk.sh:13-85` (arg parsing), `scripts/start-kiosk.sh:144-176` (command building)

- [ ] **Step 1: Add CLI flags to server.py argparse**

In `src/openflight/server.py`, after the `--gpio-debounce` argument (line 1275), add:

```python
    parser.add_argument(
        "--kld7", action="store_true", help="Enable K-LD7 angle radar module"
    )
    parser.add_argument(
        "--kld7-port", default=None, help="K-LD7 serial port (auto-detect if not specified)"
    )
    parser.add_argument(
        "--kld7-orientation",
        choices=["vertical", "horizontal"],
        default="vertical",
        help="K-LD7 mount orientation — which angle plane it measures (default: vertical)",
    )
```

- [ ] **Step 2: Add K-LD7 globals and init function**

In `src/openflight/server.py`, after the camera globals (near line 30), add:

```python
# K-LD7 angle radar
kld7_tracker = None
```

After the `init_camera()` function (after line 302), add:

```python
def init_kld7(port=None, orientation="vertical") -> bool:
    """Initialize K-LD7 angle radar tracker."""
    global kld7_tracker  # pylint: disable=global-statement
    try:
        from openflight.kld7 import KLD7Tracker

        kld7_tracker = KLD7Tracker(port=port, orientation=orientation)
        if kld7_tracker.connect():
            kld7_tracker.start()
            return True
        else:
            kld7_tracker = None
            return False
    except Exception as e:
        logger.warning("K-LD7 initialization failed: %s", e)
        kld7_tracker = None
        return False
```

- [ ] **Step 3: Wire up K-LD7 init in main()**

In `src/openflight/server.py` `main()`, after the camera init block (after line 1349), add:

```python
    # Initialize K-LD7 angle radar (if enabled)
    if args.kld7:
        if init_kld7(port=args.kld7_port, orientation=args.kld7_orientation):
            print(f"K-LD7 angle radar enabled (orientation: {args.kld7_orientation})")
        else:
            print("K-LD7 not available - running without angle radar")
    ```

- [ ] **Step 4: Add K-LD7 cleanup in finally block**

In `src/openflight/server.py`, in the `finally` block at the end of `main()` (line 1374), add before `stop_monitor()`:

```python
        if kld7_tracker:
            kld7_tracker.stop()
```

- [ ] **Step 5: Add --kld7 flags to start-kiosk.sh**

In `scripts/start-kiosk.sh`, add variable declarations after line 20:

```bash
KLD7=false
KLD7_PORT=""
KLD7_ORIENTATION=""
```

In the `while` loop (after line 76), add cases:

```bash
        --kld7)
            KLD7=true
            shift
            ;;
        --kld7-port)
            KLD7_PORT="$2"
            shift 2
            ;;
        --kld7-orientation)
            KLD7_ORIENTATION="$2"
            shift 2
            ;;
```

In the command building section (after line 176), add:

```bash
if [ "$KLD7" = true ]; then
    SERVER_CMD="$SERVER_CMD --kld7"
fi

if [ -n "$KLD7_PORT" ]; then
    SERVER_CMD="$SERVER_CMD --kld7-port $KLD7_PORT"
fi

if [ -n "$KLD7_ORIENTATION" ]; then
    SERVER_CMD="$SERVER_CMD --kld7-orientation $KLD7_ORIENTATION"
fi
```

- [ ] **Step 6: Commit**

```bash
git add src/openflight/server.py scripts/start-kiosk.sh
git commit -m "feat(kld7): add --kld7 CLI flags and server init"
```

---

### Task 4: Server Integration — Shot Angle Attachment

**Files:**
- Modify: `src/openflight/server.py:763-861` (on_shot_detected)
- Modify: `src/openflight/server.py:194-219` (shot_to_dict)
- Test: `tests/test_server.py`

- [ ] **Step 1: Write failing test for shot_to_dict with angle_source**

Add to `tests/test_server.py`:

```python
    def test_angle_source_field(self):
        """shot_to_dict should include angle_source."""
        shot = Shot(
            ball_speed_mph=150.0,
            timestamp=datetime.now(),
            launch_angle_vertical=12.5,
            launch_angle_confidence=0.8,
        )
        result = shot_to_dict(shot)
        assert "angle_source" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py::TestShotToDict::test_angle_source_field -v`
Expected: FAIL — `angle_source` not in result

- [ ] **Step 3: Add angle_source to Shot dataclass**

In `src/openflight/launch_monitor.py`, add field to Shot (after line 258):

```python
    angle_source: Optional[str] = None  # "radar", "camera", "estimated", or None
```

- [ ] **Step 4: Add angle_source to shot_to_dict**

In `src/openflight/server.py`, in `shot_to_dict()` (line 194-219), add to the return dict:

```python
        "angle_source": shot.angle_source,
```

Update the comment on line 208 from `# Launch angle from camera` to `# Launch angle data`.

- [ ] **Step 5: Add K-LD7 angle attachment to on_shot_detected**

In `src/openflight/server.py`, in `on_shot_detected()`, add a K-LD7 block **before** the camera block (before line 773). The K-LD7 data should be checked first since it's higher priority:

```python
    # Try K-LD7 angle radar first (highest priority for angle data)
    try:
        if kld7_tracker and shot.mode != "mock":
            kld7_angle = kld7_tracker.get_angle_for_shot()
            if kld7_angle:
                if kld7_angle.vertical_deg is not None:
                    shot.launch_angle_vertical = kld7_angle.vertical_deg
                    shot.launch_angle_confidence = kld7_angle.confidence
                    shot.angle_source = "radar"
                    logger.info(
                        "K-LD7 vertical angle: %.1f° (conf: %.0f%%, %d frames)",
                        kld7_angle.vertical_deg, kld7_angle.confidence * 100, kld7_angle.num_frames,
                    )
                if kld7_angle.horizontal_deg is not None:
                    shot.launch_angle_horizontal = kld7_angle.horizontal_deg
                    if shot.angle_source is None:
                        shot.angle_source = "radar"
                        shot.launch_angle_confidence = kld7_angle.confidence
                    logger.info(
                        "K-LD7 horizontal angle: %.1f° (conf: %.0f%%)",
                        kld7_angle.horizontal_deg, kld7_angle.confidence * 100,
                    )
            kld7_tracker.reset()
    except Exception as e:
        logger.warning("K-LD7 processing error: %s", e)
```

Then modify the existing camera block to only run if K-LD7 didn't provide the vertical angle. Change the condition on line 773 from:

```python
        if camera_tracker and camera_enabled and shot.mode != "mock":
```

to:

```python
        if camera_tracker and camera_enabled and shot.mode != "mock" and shot.launch_angle_vertical is None:
```

And after the camera sets the angle, add the source:

```python
                shot.angle_source = "camera"
```

In the estimation fallback block (line 804), after setting the angles, add:

```python
        shot.angle_source = "estimated"
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_server.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/openflight/server.py src/openflight/launch_monitor.py tests/test_server.py
git commit -m "feat(kld7): attach K-LD7 angle data to shots in on_shot_detected"
```

---

### Task 5: UI — Split Launch Angle into Vertical + Horizontal Cards

**Files:**
- Modify: `ui/src/types/shot.ts:1-19`
- Modify: `ui/src/components/ShotDisplay.tsx:150-199`

- [ ] **Step 1: Add angle_source to Shot TypeScript interface**

In `ui/src/types/shot.ts`, update the launch angle section (lines 10-13):

```typescript
  // Launch angle data (from K-LD7 radar, camera, or estimation)
  launch_angle_vertical: number | null;
  launch_angle_horizontal: number | null;
  launch_angle_confidence: number | null;
  angle_source: 'radar' | 'camera' | 'estimated' | null;
```

- [ ] **Step 2: Update ShotDisplay to show separate Vertical and Horizontal cards**

In `ui/src/components/ShotDisplay.tsx`, replace the single Launch Angle MetricCard (lines 177-188) with two cards:

```tsx
          <MetricCard
            value={hasLaunchAngle ? shot.launch_angle_vertical!.toFixed(1) : '—'}
            unit={hasLaunchAngle ? '°' : undefined}
            label="Vertical"
            subtext={hasLaunchAngle ? (shot.angle_source ?? undefined) : undefined}
            variant="secondary"
            confidence={hasLaunchAngle ? getLaunchAngleQuality(shot.launch_angle_confidence) : null}
          />
          {shot.launch_angle_horizontal !== null && (
            <MetricCard
              value={shot.launch_angle_horizontal.toFixed(1)}
              unit="°"
              label="Horizontal"
              subtext={shot.angle_source ?? undefined}
              variant="secondary"
              confidence={getLaunchAngleQuality(shot.launch_angle_confidence)}
            />
          )}
```

- [ ] **Step 3: Build UI and verify no errors**

Run: `cd ui && npm run build`
Expected: Build succeeds with no TypeScript errors

- [ ] **Step 4: Commit**

```bash
git add ui/src/types/shot.ts ui/src/components/ShotDisplay.tsx
git commit -m "feat(ui): split launch angle into separate Vertical and Horizontal cards"
```

---

### Task 6: Add kld7 dependency to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add kld7 as optional dependency**

In `pyproject.toml`, add a new optional dependency group for the K-LD7 module. Find the `[project.optional-dependencies]` section and add:

```toml
kld7 = [
    "kld7>=0.1.0",
]
```

Also add `kld7` to the `ui` extras group (or whichever group is used for the full install on Pi) so it's included in the standard deployment.

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "feat(kld7): add kld7 package as optional dependency"
```

---

### Task 7: Integration Test — End-to-End with Mock Data

**Files:**
- Test: `tests/test_kld7.py`

- [ ] **Step 1: Write integration test**

Add to `tests/test_kld7.py`:

```python
from openflight.launch_monitor import Shot, ClubType
from openflight.server import shot_to_dict
from datetime import datetime


class TestKLD7Integration:
    """Integration tests for K-LD7 angle data flowing through to Shot."""

    def test_angle_attaches_to_shot_vertical(self):
        """K-LD7 vertical angle should attach to Shot correctly."""
        shot = Shot(
            ball_speed_mph=150.0,
            timestamp=datetime.now(),
            launch_angle_vertical=12.5,
            launch_angle_confidence=0.8,
            angle_source="radar",
        )
        result = shot_to_dict(shot)
        assert result["launch_angle_vertical"] == 12.5
        assert result["launch_angle_confidence"] == 0.8
        assert result["angle_source"] == "radar"

    def test_angle_attaches_to_shot_horizontal(self):
        """K-LD7 horizontal angle should attach to Shot correctly."""
        shot = Shot(
            ball_speed_mph=150.0,
            timestamp=datetime.now(),
            launch_angle_horizontal=-3.5,
            launch_angle_confidence=0.7,
            angle_source="radar",
        )
        result = shot_to_dict(shot)
        assert result["launch_angle_horizontal"] == -3.5
        assert result["angle_source"] == "radar"

    def test_carry_adjusts_for_vertical_angle(self):
        """Shot carry should adjust when vertical angle is provided."""
        shot_no_angle = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
        shot_with_angle = Shot(
            ball_speed_mph=150.0,
            timestamp=datetime.now(),
            launch_angle_vertical=15.0,
            launch_angle_confidence=0.8,
            angle_source="radar",
        )
        # Both should produce valid carry, but values differ
        assert shot_no_angle.estimated_carry_yards > 0
        assert shot_with_angle.estimated_carry_yards > 0
        assert shot_no_angle.estimated_carry_yards != shot_with_angle.estimated_carry_yards

    def test_tracker_angle_to_shot_flow(self):
        """Full flow: KLD7Tracker ring buffer → get_angle → attach to Shot."""
        tracker = KLD7Tracker.__new__(KLD7Tracker)
        tracker.orientation = "vertical"
        tracker.buffer_seconds = 2.0
        tracker.max_buffer_frames = 70
        tracker._init_ring_buffer()

        now = time.time()
        # Simulate ball pass
        for i in range(3):
            tracker._add_frame(KLD7Frame(
                timestamp=now + i * 0.03,
                tdat={"distance": 2.0, "speed": 50.0, "angle": 12.0, "magnitude": 5000},
                pdat=[{"distance": 2.0, "speed": 50.0, "angle": 12.0, "magnitude": 5000}],
            ))

        angle = tracker.get_angle_for_shot()
        assert angle is not None

        shot = Shot(
            ball_speed_mph=150.0,
            timestamp=datetime.now(),
        )
        shot.launch_angle_vertical = angle.vertical_deg
        shot.launch_angle_confidence = angle.confidence
        shot.angle_source = "radar"

        result = shot_to_dict(shot)
        assert result["launch_angle_vertical"] == 12.0
        assert result["angle_source"] == "radar"
        assert result["launch_angle_confidence"] > 0.0
```

- [ ] **Step 2: Run all tests**

Run: `uv run pytest tests/test_kld7.py tests/test_server.py tests/test_launch_monitor.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_kld7.py
git commit -m "test(kld7): add integration tests for K-LD7 angle data flow"
```

---

## Verification

```bash
# All K-LD7 tests
uv run pytest tests/test_kld7.py -v

# Server tests (shot_to_dict, estimate_launch_angle)
uv run pytest tests/test_server.py -v

# Launch monitor tests (carry distance with angles)
uv run pytest tests/test_launch_monitor.py -v

# UI builds cleanly
cd ui && npm run build

# Manual test on Pi with K-LD7 connected:
scripts/start-kiosk.sh --kld7 --kld7-orientation vertical

# Manual test without K-LD7 (should work exactly as before):
scripts/start-kiosk.sh
```
