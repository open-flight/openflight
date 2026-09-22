import { io, type Socket } from 'socket.io-client';
import { useSystemStore } from '../stores/useSystemStore';
import { useShotStore } from '../stores/useShotStore';
import { useCameraStore, type CameraCaptureSettings } from '../stores/useCameraStore';
import { useDebugStore } from '../stores/useDebugStore';
import {
  type Shot,
  type SessionState,
  type TriggerDiagnostic,
  type TriggerDiagnosticUpdate,
  type TriggerStatus,
} from '../types/shot';
import type { DebugReading, RadarConfig, DebugShotLog, SimShotInfo, SimStatus } from '../types/socket';
import type { PowerStatus } from '../types/power';
import { getServerOrigin } from '../utils/serverOrigin';
import { handleShotMessage, handleShotUpdate, type ShotMessage, type ShotUpdateMessage } from './handleShotMessage';
import { ingestSessionClub } from './sessionClubSync';
import { remainingShotsAfterClear } from './sessionClear';
import { useProfileStore } from '../stores/useProfileStore';
import type { ProfilesSnapshot } from '../types/profile';

const SOCKET_URL = getServerOrigin();

class SocketService {
  private socket: Socket | null = null;
  private sessionClearedListeners = new Set<() => void>();

  connect() {
    if (this.socket) return;

    this.socket = io(SOCKET_URL, {
      transports: ['websocket', 'polling'],
    });

    this.setupListeners();
  }

  disconnect() {
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
  }

  private setupListeners() {
    if (!this.socket) return;

    this.socket.on('connect', () => {
      console.log('Connected to server');
      useSystemStore.getState().setConnected(true);
      this.socket?.emit('get_session');
      this.socket?.emit('get_trigger_status');
      this.socket?.emit('get_radar_config');
      this.socket?.emit('get_camera_capture_settings');
      this.socket?.emit('get_profiles');
    });

    this.socket.on('disconnect', () => {
      console.log('Disconnected from server');
      useSystemStore.getState().setConnected(false);
      useShotStore.getState().finishShotProcessing();
    });

    this.socket.on('shot_processing', (data: { state: 'capturing' | 'calculating' | 'failed' }) => {
      const shotStore = useShotStore.getState();
      if (data.state === 'failed') {
        shotStore.finishShotProcessing();
      } else {
        shotStore.startShotProcessing(data.state);
      }
    });

    this.socket.on('shot', (data: ShotMessage) => {
      handleShotMessage(data);
    });

    this.socket.on('shot_update', (data: ShotUpdateMessage) => {
      handleShotUpdate(data);
    });

    // Swing-speed mode also emits a normal `shot` event, handled above, so the
    // rep is already recorded. This listener is registered without a payload to
    // document that `swing_speed` is deliberately ignored here rather than
    // forgotten -- handling it too would double-count the rep.
    this.socket.on('swing_speed', () => {});

    this.socket.on('sim_status', (data: SimStatus) => {
      useSystemStore.getState().setSimStatus(data);
    });

    this.socket.on('power_status', (data: PowerStatus) => {
      useSystemStore.getState().setPowerStatus(data);
    });

    this.socket.on('sim_shot', (data: SimShotInfo) => {
      useSystemStore.getState().setLatestSimShot(data);
    });

    this.socket.on('sim_send_failed', (data: { target: string; reason: string }) => {
      console.warn(`Sim send failed (${data.target}): ${data.reason}`);
    });

    this.socket.on('sim_shot_dropped', (data: { reason: string }) => {
      console.warn(`Sim shot dropped: ${data.reason}`);
    });

    this.socket.on('club_changed', (data: { club: string }) => {
      ingestSessionClub(data.club);
    });

    this.socket.on('profiles', (data: ProfilesSnapshot) => {
      useProfileStore.getState().applySnapshot(data);
    });

    this.socket.on('session_state', (data: SessionState & { mock_mode?: boolean; debug_mode?: boolean }) => {
      console.log('Session state received:', data);
      // Need to get latest state of setShots
      useShotStore.getState().setShots(data.shots);

      const systemStore = useSystemStore.getState();
      if (data.mock_mode !== undefined) {
        systemStore.setMockMode(data.mock_mode);
      }
      if (data.debug_mode !== undefined) {
        systemStore.setDebugMode(data.debug_mode);
      }
      ingestSessionClub(data.club);
    });

    this.socket.on('debug_toggled', (data: { enabled: boolean }) => {
      useSystemStore.getState().setDebugMode(data.enabled);
      if (!data.enabled) {
        useDebugStore.getState().clearDebugData();
      }
    });

    this.socket.on('debug_shot', (data: DebugShotLog) => {
      useDebugStore.getState().addDebugShotLog(data);
    });

    this.socket.on('debug_reading', (data: DebugReading) => {
      useDebugStore.getState().addDebugReading(data);
    });

    this.socket.on('radar_config', (data: RadarConfig) => {
      useDebugStore.getState().setRadarConfig(data);
    });

    this.socket.on('camera_capture_settings', (data: CameraCaptureSettings) => {
      useCameraStore.getState().setCaptureSettings(data);
    });

    this.socket.on('camera_capture_settings_error', (data: { error: string }) => {
      useCameraStore.getState().setCaptureSettingsError(data.error);
    });

    this.socket.on('session_cleared', (data?: { profile_id?: string; shots?: Shot[] }) => {
      const remaining = remainingShotsAfterClear(useShotStore.getState().shots, data);
      if (remaining.length === 0) {
        useShotStore.getState().clearShots();
      } else {
        useShotStore.getState().setShots(remaining);
      }
      this.sessionClearedListeners.forEach((listener) => listener());
    });

    this.socket.on('trigger_diagnostic', (data: TriggerDiagnostic) => {
      const debugStore = useDebugStore.getState();
      debugStore.addTriggerDiagnostic(data);
      debugStore.updateTriggerStatusStats(data.accepted);
    });

    this.socket.on('trigger_diagnostic_update', (data: TriggerDiagnosticUpdate) => {
      useDebugStore.getState().updateTriggerDiagnostic(data);
    });

    this.socket.on('trigger_status', (data: TriggerStatus) => {
      useDebugStore.getState().setTriggerStatus(data);
    });

    this.socket.on(
      'cloud_upload_status',
      (data: { state: 'idle' | 'running' | 'complete' | 'error'; message: string }) => {
        useSystemStore.getState().setCloudUploadStatus(data.state, data.message);
      }
    );
  }

  // Emitters
  onSessionCleared(listener: () => void) {
    this.sessionClearedListeners.add(listener);
    return () => {
      this.sessionClearedListeners.delete(listener);
    };
  }

  clearSession(profileId: string) {
    this.socket?.emit('clear_session', { profile_id: profileId });
  }

  setActiveProfile(profileId: string) {
    this.socket?.emit('set_active_profile', { profile_id: profileId });
  }

  addProfile(name: string) {
    this.socket?.emit('add_profile', { name });
  }

  renameProfile(profileId: string, name: string) {
    this.socket?.emit('rename_profile', { profile_id: profileId, name });
  }

  removeProfile(profileId: string) {
    this.socket?.emit('remove_profile', { profile_id: profileId });
  }

  uploadCloud() {
    useSystemStore.getState().setCloudUploadStatus('running', 'Uploading...');
    this.socket?.emit('upload_cloud');
  }

  setClub(club: string) {
    this.socket?.emit('set_club', { club });
  }

  setTrainingImplement(implement: string) {
    this.socket?.emit('set_training_implement', { implement });
  }

  simulateShot() {
    this.socket?.emit('simulate_shot');
  }

  deleteShot(timestamp: string) {
    this.socket?.emit('delete_shot', { timestamp });
  }

  toggleDebug() {
    this.socket?.emit('toggle_debug');
  }

  setRadarConfig(config: Partial<RadarConfig>) {
    this.socket?.emit('set_radar_config', config);
  }

  setCameraCaptureSettings(settings: Partial<CameraCaptureSettings>) {
    this.socket?.emit('set_camera_capture_settings', settings);
  }
}

export const socketService = new SocketService();
