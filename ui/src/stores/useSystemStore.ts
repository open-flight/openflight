import { create } from 'zustand';
import type { PowerStatus } from '../types/power';
import type { SimShotInfo, SimStatus } from '../types/socket';

/** The server's club selection: a built-in type, plus the bag club when one is chosen. */
export interface ClubSelection {
  club: string;
  /** "" when no specific bag club is selected. */
  bagClubId: string;
}

interface SystemState {
  connected: boolean;
  mockMode: boolean;
  debugMode: boolean;
  cloudUploadState: 'idle' | 'running' | 'complete' | 'error';
  cloudUploadMessage: string;
  simStatuses: Record<string, SimStatus>;
  latestSimShots: Record<string, SimShotInfo>;
  /** A fresh object per server update, so repeating the same selection still re-applies. */
  serverClub: ClubSelection | null;
  powerStatus: PowerStatus | null;
  setConnected: (connected: boolean) => void;
  setMockMode: (mockMode: boolean) => void;
  setDebugMode: (debugMode: boolean) => void;
  setCloudUploadStatus: (state: SystemState['cloudUploadState'], message: string) => void;
  setSimStatus: (status: SimStatus) => void;
  setLatestSimShot: (shot: SimShotInfo) => void;
  setServerClub: (selection: ClubSelection | null) => void;
  setPowerStatus: (status: PowerStatus) => void;
}

export const useSystemStore = create<SystemState>((set) => ({
  connected: false,
  mockMode: false,
  debugMode: false,
  cloudUploadState: 'idle',
  cloudUploadMessage: '',
  simStatuses: {},
  latestSimShots: {},
  serverClub: null,
  powerStatus: null,
  setConnected: (connected) => set({ connected }),
  setMockMode: (mockMode) => set({ mockMode }),
  setDebugMode: (debugMode) => set({ debugMode }),
  setCloudUploadStatus: (cloudUploadState, cloudUploadMessage) => set({ cloudUploadState, cloudUploadMessage }),
  setSimStatus: (status) =>
    set((state) => ({
      simStatuses: { ...state.simStatuses, [status.target]: status },
    })),
  setLatestSimShot: (shot) =>
    set((state) => ({
      latestSimShots: { ...state.latestSimShots, [shot.target]: shot },
    })),
  setServerClub: (serverClub) => set({ serverClub }),
  setPowerStatus: (status) => set({ powerStatus: status }),
}));
