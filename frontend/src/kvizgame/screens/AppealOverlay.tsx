import type { GameState } from '../types';

interface AppealOverlayProps {
  state: GameState;
  isHost: boolean;
  send: (op: string, data?: Record<string, unknown>) => void;
}

const overlayStyle: React.CSSProperties = {
  position: 'absolute',
  inset: 0,
  background: 'rgba(0,0,0,0.75)',
  zIndex: 40,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '1.5rem',
};

export function AppealOverlay({ state, isHost, send }: AppealOverlayProps) {
  const { appeal_by, player_names } = state;
  if (!appeal_by) return null;

  const appealer = player_names[appeal_by] ?? appeal_by;

  return (
    <div style={overlayStyle}>
      <div style={{ fontSize: '2rem' }}>⚖️</div>
      <h2 style={{ color: '#ffd54f', margin: 0, textAlign: 'center' }}>
        Appeal
      </h2>
      <p style={{ color: '#fff', fontSize: '1.1rem', textAlign: 'center', margin: 0 }}>
        <strong style={{ color: '#90caf9' }}>{appealer}</strong> is disputing the judgment
      </p>

      {isHost ? (
        <div style={{ display: 'flex', gap: '1rem', marginTop: '0.5rem' }}>
          <button
            onClick={() => send('resolve_appeal', { accept: true })}
            style={{ background: '#2e7d32', color: '#fff', fontSize: '1rem', padding: '0.6rem 1.8rem', borderRadius: 6, cursor: 'pointer' }}
          >
            ✓ Accept
          </button>
          <button
            onClick={() => send('resolve_appeal', { accept: false })}
            style={{ background: '#c62828', color: '#fff', fontSize: '1rem', padding: '0.6rem 1.8rem', borderRadius: 6, cursor: 'pointer' }}
          >
            ✗ Reject
          </button>
        </div>
      ) : (
        <p style={{ color: '#aaa', margin: 0 }}>Waiting for host's decision…</p>
      )}
    </div>
  );
}
