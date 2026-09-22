/**
 * Socket.IO event handlers mirroring the Flask server contract the UI uses.
 */

import type { Server, Socket } from 'socket.io';
import type { CameraCaptureSettings } from '../src/stores/useCameraStore.js';
import type { RadarConfig } from '../src/types/socket.js';
import type { MockSession } from './session.js';

const CAMERA_CAPTURE_SETTINGS: CameraCaptureSettings = { available: false };

export function registerHandlers(io: Server, session: MockSession): void {
  io.on('connection', (socket: Socket) => {
    console.log('[mock-server] client connected');

    socket.emit('session_state', session.sessionStatePayload(true));
    socket.emit('profiles', session.snapshot());
    socket.emit('trigger_status', session.triggerStatus());
    socket.emit('radar_config', session.radarConfig);
    socket.emit('camera_capture_settings', CAMERA_CAPTURE_SETTINGS);
    socket.emit('power_status', {
      available: true,
      provider: 'mock',
      state: 'on_battery',
      battery_percent: 78,
      battery_voltage_v: 3.91,
      external_power: false,
      updated_at: new Date().toISOString(),
      error: null,
    });

    socket.on('get_session', () => {
      socket.emit('session_state', session.sessionStatePayload(true));
    });

    socket.on('get_trigger_status', () => {
      socket.emit('trigger_status', session.triggerStatus());
    });

    socket.on('get_radar_config', () => {
      socket.emit('radar_config', session.radarConfig);
    });

    socket.on('get_camera_capture_settings', () => {
      socket.emit('camera_capture_settings', CAMERA_CAPTURE_SETTINGS);
    });

    socket.on('get_debug_status', () => {
      socket.emit('debug_status', {
        enabled: session.debugMode,
        log_path: null,
      });
    });

    socket.on('simulate_shot', () => {
      io.emit('shot_processing', { state: 'capturing' });
      setTimeout(() => io.emit('shot_processing', { state: 'calculating' }), 350);
      setTimeout(() => {
        const { shot, stats } = session.simulateShot();
        io.emit('shot', { shot, stats });
        io.emit('trigger_status', session.triggerStatus());
      }, 1400);
    });

    socket.on('set_club', (data: { club?: string }) => {
      const club = session.setClub(data?.club ?? 'driver');
      io.emit('club_changed', { club });
    });

    socket.on('set_training_implement', (data: { implement?: string }) => {
      const implement = session.setTrainingImplement(data?.implement ?? 'driver');
      io.emit('training_implement_changed', {
        implement,
        label: implement,
      });
    });

    const emitProfiles = () => io.emit('profiles', session.snapshot());

    socket.on('get_profiles', emitProfiles);

    socket.on('set_active_profile', (data: { profile_id?: string }) => {
      session.setActiveProfile(data?.profile_id);
      emitProfiles();
    });

    socket.on('add_profile', (data: { name?: string }) => {
      session.addProfile(data?.name);
      emitProfiles();
    });

    socket.on('rename_profile', (data: { profile_id?: string; name?: string }) => {
      session.renameProfile(data?.profile_id, data?.name);
      emitProfiles();
    });

    socket.on('remove_profile', (data: { profile_id?: string }) => {
      session.removeProfile(data?.profile_id);
      emitProfiles();
    });

    socket.on('clear_session', (data?: { profile_id?: string }) => {
      const profileId = data?.profile_id || session.activeProfile.id;
      session.clearProfile(profileId);
      io.emit('session_cleared', { profile_id: profileId, shots: session.shots });
    });

    socket.on('delete_shot', (data: { timestamp?: string }) => {
      const deleted = session.deleteShot(data?.timestamp);
      if (!deleted) {
        socket.emit('delete_shot_error', { error: 'Shot not found' });
        return;
      }
      io.emit('session_state', session.sessionStatePayload(true));
    });

    socket.on('upload_cloud', () => {
      io.emit('cloud_upload_status', {
        state: 'running',
        message: 'Uploading...',
      });
      setTimeout(() => {
        io.emit('cloud_upload_status', {
          state: 'complete',
          message: 'Mock upload complete',
        });
      }, 400);
    });

    socket.on('toggle_debug', () => {
      const enabled = session.toggleDebug();
      io.emit('debug_toggled', {
        enabled,
        log_path: enabled ? '/tmp/openflight-mock-debug.jsonl' : undefined,
      });
    });

    socket.on('set_radar_config', (data: Partial<RadarConfig>) => {
      const config = session.updateRadarConfig(data ?? {});
      io.emit('radar_config', config);
    });

    socket.on('set_camera_capture_settings', () => {
      socket.emit('camera_capture_settings_error', {
        error: 'High-speed camera capture is not running',
      });
    });

    socket.on('disconnect', () => {
      console.log('[mock-server] client disconnected');
    });
  });
}
