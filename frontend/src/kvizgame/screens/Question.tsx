import { useEffect, useRef, useState } from 'react';
import type { GameState } from '../types';
import { Scores } from './Scores';
import { TimerBorder } from './TimerBorder';
import { PauseOverlay } from './PauseOverlay';
import { AppealOverlay } from './AppealOverlay';

const MEDIA_FOLDER: Record<string, string> = {
  image: 'Images',
  voice: 'Audio',
  audio: 'Audio',
  video: 'Video',
};

interface QuestionProps {
  state: GameState;
  channelId: string;
  playerId: string;
  isHost: boolean;
  send: (op: string, data?: Record<string, unknown>) => void;
}

export function Question({ state, channelId, playerId, isHost, send }: QuestionProps) {
  const { phase, paused, appeal_by, last_judged_id, current_question: cq, active_player_id, current_answerer_id, scores, player_names } = state;
  const isActive = active_player_id === playerId;
  const isAnswerer = current_answerer_id === playerId;

  const mediaRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!mediaRef.current) return;
    mediaRef.current.querySelectorAll<HTMLMediaElement>('audio, video').forEach(el => {
      el.play().catch(() => {});
    });
  }, [cq?.theme_name, cq?.price]);

  return (
    <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', height: '100%', padding: '1rem', gap: '1rem' }}>
      <TimerBorder phase={phase} paused={paused} />
      {appeal_by ? (
        <AppealOverlay state={state} isHost={isHost} send={send} />
      ) : (
        paused && <PauseOverlay isHost={isHost} send={send} />
      )}
      {isHost && !paused && !appeal_by && (
        <button
          onClick={() => send('pause')}
          style={{ position: 'absolute', top: '0.5rem', right: '0.5rem', background: 'transparent', color: '#90caf9', border: '1px solid #1976d2', borderRadius: 4, padding: '0.2rem 0.6rem', fontSize: '0.8rem', cursor: 'pointer', zIndex: 5 }}
        >
          ⏸
        </button>
      )}
      {cq && (
        <div style={{ textAlign: 'center', color: '#90caf9', fontSize: '0.85rem' }}>
          {cq.theme_name} · <strong style={{ color: '#ffd54f' }}>{cq.price}</strong>
        </div>
      )}

      <div ref={mediaRef} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '1rem' }}>
        {cq?.scenario.map((atom, i) => {
          if (atom.type === 'text' || atom.type === 'say') {
            return <p key={i} style={{ fontSize: '1.4rem', textAlign: 'center', maxWidth: 600 }}>{atom.content}</p>;
          }
          const folder = MEDIA_FOLDER[atom.type];
          if (folder) {
            const filename = atom.content.replace(/^@/, '');
            const url = `/api/media/${channelId}/${folder}/${encodeURIComponent(filename)}`;
            if (atom.type === 'image') {
              return <img key={i} src={url} style={{ maxWidth: '100%', maxHeight: 400, objectFit: 'contain' }} />;
            }
            if (atom.type === 'audio' || atom.type === 'voice') {
              return <audio key={i} autoPlay controls src={url} style={{ width: '100%', maxWidth: 500 }} />;
            }
            if (atom.type === 'video') {
              return <video key={i} autoPlay controls src={url} style={{ maxWidth: '100%', maxHeight: 400 }} />;
            }
          }
          return null;
        })}
      </div>

      {phase === 'ANSWER_RESULT' && cq && (
        <div style={{ textAlign: 'center', color: '#a5d6a7', fontSize: '1rem' }}>
          Answer: <strong>{cq.right.join(' / ')}</strong>
        </div>
      )}

      <Controls
        phase={phase}
        isActive={isActive}
        isHost={isHost}
        isAnswerer={isAnswerer}
        answerer={current_answerer_id}
        cq={cq}
        playerId={playerId}
        playerNames={player_names}
        lastJudgedId={last_judged_id}
        send={send}
      />

      <Scores scores={scores} playerNames={player_names} answerer={current_answerer_id} />
    </div>
  );
}

interface ControlsProps {
  phase: GameState['phase'];
  isActive: boolean;
  isHost: boolean;
  isAnswerer: boolean;
  answerer: string | null;
  cq: GameState['current_question'];
  playerId: string;
  playerNames: Record<string, string>;
  lastJudgedId: string | null;
  send: (op: string, data?: Record<string, unknown>) => void;
}

function Controls({ phase, isActive, isHost, isAnswerer, answerer, cq, playerId, playerNames, lastJudgedId, send }: ControlsProps) {
  const minBid = cq ? Math.max(1, cq.price) : 1;
  const [bid, setBid] = useState(minBid);
  useEffect(() => { setBid(minBid); }, [minBid]);

  if (phase === 'AUCTION_BIDDING') {
    if (!isActive) {
      return <div style={{ textAlign: 'center', color: '#ffd54f' }}>Auction! Waiting for bid…</div>;
    }
    return (
      <div style={{ textAlign: 'center' }}>
        <p style={{ color: '#ffd54f', marginBottom: '0.5rem' }}>Place your bid (min {minBid}):</p>
        <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'center', alignItems: 'center' }}>
          <input
            type="number"
            min={minBid}
            value={bid}
            onChange={e => setBid(Math.max(minBid, +e.target.value))}
            style={{ width: 100, fontSize: '1rem', padding: '0.4rem', background: '#1a2035', color: '#fff', border: '1px solid #1976d2', borderRadius: 4 }}
          />
          <button
            onClick={() => send('bid', { amount: bid })}
            disabled={bid < minBid}
            style={{ background: '#1565c0', color: '#fff', fontSize: '1rem', padding: '0.5rem 1.5rem' }}
          >
            Bid
          </button>
        </div>
      </div>
    );
  }

  if (phase === 'CAT_TRANSFER') {
    if (!isActive) {
      return <div style={{ textAlign: 'center', color: '#ffd54f' }}>Cat in a bag! Waiting for transfer…</div>;
    }
    const others = Object.entries(playerNames).filter(([id]) => id !== playerId);
    return (
      <div style={{ textAlign: 'center' }}>
        <p style={{ color: '#ffd54f', marginBottom: '0.5rem' }}>Cat in a bag! Choose a player:</p>
        <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'center', flexWrap: 'wrap' }}>
          {others.map(([id, name]) => (
            <button
              key={id}
              onClick={() => send('transfer', { recipient_id: id })}
              style={{ background: '#6a1b9a', color: '#fff', fontSize: '1rem', padding: '0.5rem 1.5rem', borderRadius: 6 }}
            >
              {name}
            </button>
          ))}
        </div>
      </div>
    );
  }

  if (phase === 'QUESTION' && isHost) {
    return (
      <div style={{ textAlign: 'center' }}>
        <button onClick={() => send('open_buzzer')} style={{ background: '#1565c0', color: '#fff', fontSize: '1rem', padding: '0.6rem 2rem' }}>
          Open Buzzer
        </button>
      </div>
    );
  }

  if (phase === 'BUZZER_OPEN') {
    return (
      <div style={{ textAlign: 'center' }}>
        <button
          onClick={() => send('buzz')}
          style={{ background: '#b71c1c', color: '#fff', fontSize: '1.6rem', fontWeight: 700, padding: '1rem 3rem', borderRadius: 12 }}
        >
          BUZZ!
        </button>
      </div>
    );
  }

  if (phase === 'ANSWERING') {
    return (
      <div style={{ textAlign: 'center', color: '#ffd54f' }}>
        {isAnswerer ? 'Your turn to answer!' : `${playerNames[answerer!] ?? answerer} is answering…`}
        {isHost && (
          <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center', marginTop: '0.8rem' }}>
            <button onClick={() => send('judge', { correct: true })} style={{ background: '#2e7d32', color: '#fff', fontSize: '1rem', padding: '0.6rem 1.5rem' }}>✓ Correct</button>
            <button onClick={() => send('judge', { correct: false })} style={{ background: '#c62828', color: '#fff', fontSize: '1rem', padding: '0.6rem 1.5rem' }}>✗ Wrong</button>
          </div>
        )}
      </div>
    );
  }

  if (phase === 'ANSWER_RESULT') {
    const canAppeal = !isHost && lastJudgedId === playerId;
    return (
      <div style={{ textAlign: 'center', display: 'flex', gap: '0.75rem', justifyContent: 'center' }}>
        {isHost && (
          <button onClick={() => send('advance')} style={{ background: '#1565c0', color: '#fff', padding: '0.6rem 2rem' }}>
            Continue →
          </button>
        )}
        {canAppeal && (
          <button
            onClick={() => send('request_appeal')}
            style={{ background: '#e65100', color: '#fff', padding: '0.6rem 1.5rem', borderRadius: 6, cursor: 'pointer' }}
          >
            ⚖️ Appeal
          </button>
        )}
      </div>
    );
  }

  return null;
}
