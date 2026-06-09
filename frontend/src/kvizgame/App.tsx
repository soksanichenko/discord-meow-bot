import { useEffect, useState } from 'react';
import { setup } from './discord';
import type { AuthResult } from './discord';
import { useGame } from './useGame';
import type { GameState } from './types';
import { Board } from './screens/Board';
import { Question } from './screens/Question';
import { Scores } from './screens/Scores';

export function App() {
  const [auth, setAuth] = useState<AuthResult | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);

  useEffect(() => {
    setup().then(setAuth).catch((e: unknown) => setAuthError(String(e)));
  }, []);

  if (authError) return <Centered>Discord auth failed: {authError}</Centered>;
  if (!auth) return <Centered>Connecting to Discord…</Centered>;
  return <Game auth={auth} />;
}

function Game({ auth }: { auth: AuthResult }) {
  const { state, error, reconnecting, send } = useGame(auth.channelId, auth.userId);

  if (error) return <Centered style={{ color: '#ef9a9a' }}>{error}</Centered>;
  if (!state) return <Centered>{reconnecting ? 'Reconnecting…' : 'Joining game…'}</Centered>;

  return <Screen state={state} playerId={auth.userId} send={send} />;
}

const QUESTION_PHASES: GameState['phase'][] = [
  'QUESTION', 'AUCTION_BIDDING', 'CAT_TRANSFER',
  'BUZZER_OPEN', 'ANSWERING', 'ANSWER_RESULT',
];

function Screen({
  state,
  playerId,
  send,
}: {
  state: GameState;
  playerId: string;
  send: (op: string, d?: Record<string, unknown>) => void;
}) {
  const { phase } = state;

  if (phase === 'BOARD') {
    return <Board state={state} playerId={playerId} send={send} />;
  }

  if (QUESTION_PHASES.includes(phase)) {
    return <Question state={state} playerId={playerId} send={send} />;
  }

  if (phase === 'ROUND_END') {
    const isActive = state.active_player_id === playerId;
    return (
      <Centered>
        <h2 style={{ marginBottom: '1rem' }}>Round over</h2>
        <Scores scores={state.scores} playerNames={state.player_names} />
        {isActive && (
          <button onClick={() => send('next_round')} style={{ marginTop: '1.5rem', background: '#1565c0', color: '#fff', padding: '0.6rem 2rem' }}>
            Next Round →
          </button>
        )}
      </Centered>
    );
  }

  if (phase === 'GAME_OVER') {
    return (
      <Centered>
        <h2 style={{ marginBottom: '1rem', color: '#ffd54f' }}>Game Over</h2>
        <Scores scores={state.scores} playerNames={state.player_names} />
      </Centered>
    );
  }

  return <Centered>Phase: {phase}</Centered>;
}

function Centered({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: '1rem', ...style }}>
      {children}
    </div>
  );
}
