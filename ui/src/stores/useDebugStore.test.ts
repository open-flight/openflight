import { beforeEach, describe, expect, it } from 'vitest';

import { useDebugStore } from './useDebugStore';
import type { TriggerDiagnostic } from '../types/shot';

const diagnostic: TriggerDiagnostic = {
  timestamp: '2026-08-15T10:00:00.000',
  trigger_type: 'sound',
  accepted: true,
  reason: 'accepted',
  response_bytes: 41000,
  total_readings: 20,
  outbound_readings: 10,
  inbound_readings: 10,
  peak_outbound_mph: 100,
  peak_inbound_mph: 75,
  all_outbound_speeds: [],
  all_inbound_speeds: [],
  peak_outbound_magnitude: 0,
  peak_inbound_magnitude: 0,
  latency_ms: 25,
};

describe('useDebugStore IWR6843 diagnostics', () => {
  beforeEach(() => {
    useDebugStore.setState({
      triggerDiagnostics: [],
      iwr6843Alert: null,
      iwr6843ErrorActive: false,
    });
  });

  it('updates the matching trigger row instead of adding another history item', () => {
    const store = useDebugStore.getState();
    store.addTriggerDiagnostic(diagnostic);
    store.updateTriggerDiagnostic({
      timestamp: diagnostic.timestamp,
      iwr6843: { state: 'rejected', reason: 'rejected_track_quality' },
    });

    const state = useDebugStore.getState();
    expect(state.triggerDiagnostics).toHaveLength(1);
    expect(state.triggerDiagnostics[0].iwr6843).toEqual({
      state: 'rejected',
      reason: 'rejected_track_quality',
    });
    expect(state.iwr6843Alert).toBeNull();
  });

  it('raises one dismissible alert for a capture error until the radar recovers', () => {
    const store = useDebugStore.getState();
    store.addTriggerDiagnostic(diagnostic);
    store.updateTriggerDiagnostic({
      timestamp: diagnostic.timestamp,
      iwr6843: { state: 'error', reason: "bad magic b'l3du'" },
    });

    expect(useDebugStore.getState().iwr6843Alert?.reason).toBe("bad magic b'l3du'");
    useDebugStore.getState().dismissIWR6843Alert();
    expect(useDebugStore.getState().iwr6843Alert).toBeNull();

    useDebugStore.getState().updateTriggerDiagnostic({
      timestamp: diagnostic.timestamp,
      iwr6843: { state: 'error', reason: 'another capture error' },
    });
    expect(useDebugStore.getState().iwr6843Alert).toBeNull();

    useDebugStore.getState().updateTriggerDiagnostic({
      timestamp: diagnostic.timestamp,
      iwr6843: { state: 'accepted', reason: 'accepted', angle_deg: 20.1 },
    });
    expect(useDebugStore.getState().iwr6843ErrorActive).toBe(false);
  });

  it('does not raise the TI alert when only the onboard result is missing', () => {
    const store = useDebugStore.getState();
    store.addTriggerDiagnostic(diagnostic);
    store.updateTriggerDiagnostic({
      timestamp: diagnostic.timestamp,
      iwr6843: { state: 'no_onboard', reason: 'no onboard result; --debug readback #7 saved' },
    });

    const state = useDebugStore.getState();
    expect(state.triggerDiagnostics[0].iwr6843?.state).toBe('no_onboard');
    expect(state.iwr6843Alert).toBeNull();
    expect(state.iwr6843ErrorActive).toBe(false);
  });
});
