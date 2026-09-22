import type { SessionStats, Shot } from '../types/shot';
import { useShotStore } from '../stores/useShotStore';
import { playSwingCapturedCue } from '../utils/audioCue';

export interface ShotMessage {
  shot: Shot;
  stats: SessionStats;
  pending?: {
    iwr6843?: boolean;
    camera?: boolean;
  };
}

export interface ShotUpdateMessage {
  shot: Shot;
  stats: SessionStats;
  pending?: Record<string, boolean>;
  enrichment?: {
    status: 'skipped';
    reason: string;
    hardware: string[];
  };
}

export function handleShotMessage(data: ShotMessage) {
  const shotStore = useShotStore.getState();
  shotStore.addShot(data.shot);
  if (data.pending?.iwr6843 && data.pending?.camera) {
    shotStore.startShotProcessing('hardware_enrichment', data.shot.timestamp);
  } else if (data.pending?.iwr6843) {
    shotStore.startShotProcessing('iwr_dump', data.shot.timestamp);
  } else if (data.pending?.camera) {
    shotStore.startShotProcessing('camera_processing', data.shot.timestamp);
  }
  playSwingCapturedCue();
}

export function handleShotUpdate(data: ShotUpdateMessage) {
  useShotStore.getState().updateShot(data.shot);
}
