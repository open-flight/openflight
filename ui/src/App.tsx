import { useState, useEffect } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useSocket } from './hooks/useSocket';
import { useSystemStore } from './stores/useSystemStore';
import { useShotStore } from './stores/useShotStore';
import { useCameraStore } from './stores/useCameraStore';
import { useDebugStore } from './stores/useDebugStore';
import { useProfileStore } from './stores/useProfileStore';
import { useHeroMetricStore } from './stores/useHeroMetricStore';
import { useOnboardingStore } from './stores/useOnboardingStore';
import { useCameraReplayController } from './hooks/useCameraReplayController';
import { socketService } from './services/socketService';
import { DebugPanel } from './components/DebugPanel';
import { DisplayMode } from './components/DisplayMode';
import { OnboardingFlow } from './components/onboarding';
import { SimShotBadges } from './components/SimShotBadges';
import { ShotProcessingArea } from './components/ShotProcessingArea';
import { ShutdownDialog, type ShutdownState } from './components/ShutdownDialog';
import { CameraReplayDialog } from './components/CameraReplayDialog';
import {
  CameraPanel,
  LivePanel,
  ProfileNameDialog,
  ProfilesPanel,
  ClearSessionDialog,
  SimulateBubble,
  MenuSheet,
  PanelFooter,
  PanelHeader,
  PanelAction,
  PickerOverlay,
  ShotsPanel,
  StatsPanel,
  clubSections,
  trainingImplementSections,
  type PanelView,
} from './components/panel';
import { shouldEnableLiveBallWarning } from './components/panel/liveMetrics';
import { filterShotsByProfile } from './types/shot';
import type { Profile } from './types/profile';
import { getClubName } from './data/clubs';
import { getTrainingImplementLabel } from './data/trainingImplements';
import { unlockAudioCue } from './utils/audioCue';
import { useLaunchDaddy, LaunchDaddyOverlay, LaunchDaddyBrand } from './components/LaunchDaddy';

import { useI18n } from './i18n/useI18n';
import './components/panel/panel.css';

function AppContent() {
  const { t } = useI18n();
  const { shutdown } = useSocket();
  const { connected, mockMode, debugMode, latestSimShots, serverClub } = useSystemStore(
    useShallow((state) => ({
      connected: state.connected,
      mockMode: state.mockMode,
      debugMode: state.debugMode,
      latestSimShots: state.latestSimShots,
      serverClub: state.serverClub,
    }))
  );
  const { latestShot, shots, isNewShot, shotProcessingPhase, shotVersion } = useShotStore(
    useShallow((state) => ({
      latestShot: state.latestShot,
      shots: state.shots,
      isNewShot: state.isNewShot,
      shotProcessingPhase: state.shotProcessingPhase,
      shotVersion: state.shotVersion,
    }))
  );
  const { cameraStatus, captureSettings, captureSettingsError } = useCameraStore(
    useShallow((state) => ({
      cameraStatus: state.cameraStatus,
      captureSettings: state.captureSettings,
      captureSettingsError: state.captureSettingsError,
    }))
  );
  const { profiles, activeProfileId, profilesLoaded } = useProfileStore(
    useShallow((state) => ({
      profiles: state.profiles,
      activeProfileId: state.activeProfileId,
      profilesLoaded: state.loaded,
    }))
  );
  const activeProfile = profiles.find((profile) => profile.id === activeProfileId) ?? null;
  const activeProfileName = activeProfile?.name ?? '';
  const { heroMetricId, setHeroMetricId } = useHeroMetricStore(
    useShallow((state) => ({ heroMetricId: state.heroMetricId, setHeroMetricId: state.setHeroMetricId }))
  );
  const {
    debugReadings,
    debugShotLogs,
    radarConfig,
    triggerDiagnostics,
    triggerStatus,
    iwr6843Alert,
    dismissIWR6843Alert,
  } = useDebugStore(
    useShallow((state) => ({
      debugReadings: state.debugReadings,
      debugShotLogs: state.debugShotLogs,
      radarConfig: state.radarConfig,
      triggerDiagnostics: state.triggerDiagnostics,
      triggerStatus: state.triggerStatus,
      iwr6843Alert: state.iwr6843Alert,
      dismissIWR6843Alert: state.dismissIWR6843Alert,
    }))
  );

  // Hook subscribe so complete() re-renders on the client. Gate with getState()
  // because renderToString uses Zustand's initial snapshot, not later setState.
  useOnboardingStore((state) => state.completed);
  const onboardingCompleted = useOnboardingStore.getState().completed;
  useSystemStore((state) => state.shutdownDialogOpen);
  const shutdownDialogOpen = useSystemStore.getState().shutdownDialogOpen;
  const [currentView, setCurrentView] = useState<PanelView>('live');
  const [selectedClub, setSelectedClub] = useState('driver');
  const [selectedTrainingImplement, setSelectedTrainingImplement] = useState('driver');
  const [menuOpen, setMenuOpen] = useState(false);
  const [shutdownState, setShutdownState] = useState<ShutdownState>('confirm');
  // Open on later launches after onboarding so the club is confirmed before
  // the first shot; first-run starts closed because the wizard replaces the
  // shell. The /display route returns early below, so this never appears in
  // the passive TV view.
  const [pickerOpen, setPickerOpen] = useState(() => useOnboardingStore.getState().completed);
  const [profileDialog, setProfileDialog] = useState<{ mode: 'add' | 'rename'; target: Profile | null } | null>(null);
  const [profileDialogName, setProfileDialogName] = useState('');
  const [clearSessionOpen, setClearSessionOpen] = useState(false);
  const { activeReplay, openReplay, closeReplay, reportPlaybackError } = useCameraReplayController();

  // Reflect a server-pushed club change (e.g. the club changed in the connected
  // simulator) locally without echoing back. Done during render (React's "adjust
  // state when an input changes" pattern) rather than in an effect.
  const [appliedServerClub, setAppliedServerClub] = useState<string | null>(null);
  if (serverClub && serverClub !== appliedServerClub) {
    setAppliedServerClub(serverClub);
    setSelectedClub(serverClub);
  }

  const { isLaunchDaddyMode, isExploding, triggerExplosion } = useLaunchDaddy();
  const isDisplayRoute = typeof window !== 'undefined' && window.location.pathname.replace(/\/$/, '') === '/display';
  const isSwingSpeedMode = triggerStatus.mode === 'swing-speed';
  const activeImplementLabel = isSwingSpeedMode
    ? getTrainingImplementLabel(selectedTrainingImplement)
    : getClubName(selectedClub);

  useEffect(() => {
    return socketService.onSessionCleared(() => {
      setClearSessionOpen(false);
      setCurrentView('live');
    });
  }, []);

  // Trigger explosion when a new shot is detected in Launch Daddy mode
  useEffect(() => {
    if (isNewShot && isLaunchDaddyMode) {
      triggerExplosion();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- shotVersion triggers the effect; isNewShot is only a guard
  }, [shotVersion, isLaunchDaddyMode, triggerExplosion]);

  useEffect(() => {
    const unlock = () => unlockAudioCue();
    window.addEventListener('pointerdown', unlock, { once: true });
    window.addEventListener('keydown', unlock, { once: true });

    return () => {
      window.removeEventListener('pointerdown', unlock);
      window.removeEventListener('keydown', unlock);
    };
  }, []);

  const handleSelectProfile = (profileId: string) => {
    socketService.setActiveProfile(profileId);
    setCurrentView('live');
  };

  const handleRemoveProfile = (profileId: string) => {
    // The server refuses to remove the active profile; don't offer it either.
    if (profileId === activeProfileId) return;
    socketService.removeProfile(profileId);
  };

  const openAddProfile = () => {
    setProfileDialog({ mode: 'add', target: null });
    setProfileDialogName('');
  };

  const openRenameProfile = (profile: Profile) => {
    setProfileDialog({ mode: 'rename', target: profile });
    setProfileDialogName(profile.name);
  };

  const closeProfileDialog = () => {
    setProfileDialog(null);
    setProfileDialogName('');
  };

  const handleConfirmProfileDialog = () => {
    const name = profileDialogName.trim();
    if (!name || !profileDialog) return;
    if (profileDialog.mode === 'add') {
      socketService.addProfile(name);
    } else if (profileDialog.target) {
      socketService.renameProfile(profileDialog.target.id, name);
    }
    closeProfileDialog();
  };

  const handlePickerSelect = (id: string) => {
    if (isSwingSpeedMode) {
      setSelectedTrainingImplement(id);
      socketService.setTrainingImplement(id);
    } else {
      setSelectedClub(id);
      socketService.setClub(id);
    }
    setPickerOpen(false);
  };

  const handleShutdown = async () => {
    setShutdownState('pending');
    try {
      await shutdown();
    } catch {
      setShutdownState('error');
    }
  };

  const closeShutdown = () => {
    useSystemStore.getState().closeShutdownDialog();
    setShutdownState('confirm');
  };

  const profileShots = filterShotsByProfile(shots, activeProfileId);
  const profileLatestShot = profileShots[profileShots.length - 1] ?? null;
  const profileIsNewShot = Boolean(
    isNewShot && latestShot && profileLatestShot && latestShot.timestamp === profileLatestShot.timestamp
  );

  if (isDisplayRoute) {
    return <DisplayMode connected={connected} cameraStatus={cameraStatus} latestShot={latestShot} shots={shots} />;
  }

  if (!onboardingCompleted) {
    return <OnboardingFlow onFinished={() => setPickerOpen(false)} />;
  }

  const changeClubAction = (
    <PanelAction onClick={() => setPickerOpen(true)}>
      {isSwingSpeedMode ? t('app.changeImplement') : t('app.changeClub')}
    </PanelAction>
  );
  const latestReplay = profileLatestShot?.camera_replay;

  const liveHeaderActions = (
    <>
      {latestReplay ? (
        <PanelAction variant="secondary" onClick={() => openReplay(latestReplay)}>
          {t('replay.open')}
        </PanelAction>
      ) : null}
      {changeClubAction}
    </>
  );

  const addProfileAction = <PanelAction onClick={openAddProfile}>{t('menu.addProfile')}</PanelAction>;

  const clearSessionAction = (
    <PanelAction variant="danger" onClick={() => setClearSessionOpen(true)}>
      {t('app.clearSession')}
    </PanelAction>
  );

  const debugRecordAction = (
    <PanelAction variant="secondary" onClick={() => socketService.toggleDebug()}>
      {debugMode ? t('app.stopRecording') : t('app.record')}
    </PanelAction>
  );

  return (
    <div className={`panel-app ${isLaunchDaddyMode ? 'app--launch-daddy' : ''} ${isExploding ? 'app--exploding' : ''}`}>
      <LaunchDaddyOverlay />

      {iwr6843Alert && (
        <div className="iwr-alert" role="alert">
          <div>
            <strong>{t('live.tiRadarFailed')}</strong>
            <span>{t('live.tiRadarDetail', { reason: iwr6843Alert.reason })}</span>
          </div>
          <button type="button" onClick={dismissIWR6843Alert} aria-label={t('live.dismissAlert')}>
            {t('live.dismiss')}
          </button>
        </div>
      )}

      {shutdownDialogOpen ? (
        <ShutdownDialog state={shutdownState} onConfirm={handleShutdown} onCancel={closeShutdown} />
      ) : null}

      {activeReplay ? (
        <CameraReplayDialog
          replay={activeReplay.replay}
          state={activeReplay.state}
          onClose={closeReplay}
          onRetry={() => openReplay(activeReplay.replay)}
          onPlaybackError={reportPlaybackError}
        />
      ) : null}

      <main className="panel-app__main">
        {currentView === 'live' && (
          <>
            <ShotProcessingArea phase={shotProcessingPhase}>
              <LivePanel
                key={shotVersion}
                shot={profileLatestShot}
                shots={shots}
                profileId={activeProfileId}
                profileName={activeProfileName}
                clubLabel={activeImplementLabel}
                activeTrainingImplement={isSwingSpeedMode ? selectedTrainingImplement : undefined}
                selectedMetricId={heroMetricId}
                onSelectMetric={setHeroMetricId}
                isNewShot={profileIsNewShot}
                ballDetectionEnabled={shouldEnableLiveBallWarning(currentView, cameraStatus)}
                ballDetected={cameraStatus.ball_detected}
                headerAction={liveHeaderActions}
              />
            </ShotProcessingArea>
            {debugMode && <SimShotBadges latestSimShots={latestSimShots} />}
          </>
        )}
        {currentView === 'profiles' && (
          <ProfilesPanel
            profiles={profiles}
            activeProfileId={activeProfileId}
            shots={shots}
            loaded={profilesLoaded}
            onSelectProfile={handleSelectProfile}
            onRenameProfile={openRenameProfile}
            onRemoveProfile={handleRemoveProfile}
            headerAction={addProfileAction}
          />
        )}
        {currentView === 'stats' && (
          <StatsPanel
            shots={shots}
            activeClub={selectedClub}
            profileId={activeProfileId}
            profileName={activeProfileName}
            headerAction={clearSessionAction}
          />
        )}
        {currentView === 'shots' && (
          <ShotsPanel
            shots={shots}
            profileId={activeProfileId}
            profileName={activeProfileName}
            clubLabel={activeImplementLabel}
            onDeleteShot={(timestamp) => socketService.deleteShot(timestamp)}
            onReplayShot={(shot) => {
              if (shot.camera_replay) openReplay(shot.camera_replay);
            }}
          />
        )}
        {currentView === 'camera' && (
          <CameraPanel
            cameraStatus={cameraStatus}
            clubLabel={activeImplementLabel}
            captureSettings={captureSettings}
            captureSettingsError={captureSettingsError}
            onToggleCamera={() => socketService.toggleCamera()}
            onToggleStream={() => socketService.toggleCameraStream()}
            onUpdateCaptureSettings={(settings) => socketService.setCameraCaptureSettings(settings)}
          />
        )}
        {currentView === 'debug' && (
          <div className="panel">
            <PanelHeader
              title={t('nav.debug')}
              subtitle={debugMode ? t('app.debugRecording') : t('app.debugIdle')}
              actions={debugRecordAction}
            />
            <div className="panel__body panel-app__debug">
              <DebugPanel
                enabled={debugMode}
                readings={debugReadings}
                shotLogs={debugShotLogs}
                radarConfig={radarConfig}
                cameraStatus={cameraStatus}
                mockMode={mockMode}
                onToggle={() => socketService.toggleDebug()}
                onUpdateConfig={(config) => socketService.setRadarConfig(config)}
                triggerDiagnostics={triggerDiagnostics}
                triggerStatus={triggerStatus}
              />
            </div>
          </div>
        )}
        {mockMode && currentView === 'live' ? (
          <SimulateBubble
            label={isSwingSpeedMode ? t('app.simulateSwing') : t('app.simulateShot')}
            onSimulate={() => socketService.simulateShot()}
          />
        ) : null}
      </main>

      {/*
       * Overlays sit outside <main> so they cover the footer too, matching how
       * 6a draws them over the whole card.
       */}
      {menuOpen ? <MenuSheet onClose={() => setMenuOpen(false)} /> : null}

      {profileDialog ? (
        <ProfileNameDialog
          mode={profileDialog.mode}
          name={profileDialogName}
          onChange={setProfileDialogName}
          onConfirm={handleConfirmProfileDialog}
          onCancel={closeProfileDialog}
        />
      ) : null}

      {clearSessionOpen ? (
        <ClearSessionDialog
          profileName={activeProfileName}
          onConfirm={() => socketService.clearSession(activeProfileId)}
          onCancel={() => setClearSessionOpen(false)}
        />
      ) : null}

      {pickerOpen ? (
        <PickerOverlay
          title={isSwingSpeedMode ? t('app.selectImplement') : t('app.selectClub')}
          selectedId={isSwingSpeedMode ? selectedTrainingImplement : selectedClub}
          sections={isSwingSpeedMode ? trainingImplementSections() : clubSections()}
          onSelect={handlePickerSelect}
          onClose={() => setPickerOpen(false)}
          wide={isSwingSpeedMode}
        />
      ) : null}

      <PanelFooter
        currentView={currentView}
        onChangeView={setCurrentView}
        onOpenMenu={() => setMenuOpen((open) => !open)}
        menuOpen={menuOpen}
        shotCount={profileShots.length}
        cameraStreaming={cameraStatus.streaming}
        ballDetected={cameraStatus.ball_detected}
        debugRecording={debugMode}
        brand={isLaunchDaddyMode ? <LaunchDaddyBrand /> : undefined}
      />
    </div>
  );
}

function App() {
  return <AppContent />;
}

export default App;
