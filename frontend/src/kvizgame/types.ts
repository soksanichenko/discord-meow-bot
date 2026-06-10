export type Phase =
  | 'BOARD'
  | 'AUCTION_BIDDING'
  | 'CAT_TRANSFER'
  | 'QUESTION'
  | 'BUZZER_OPEN'
  | 'ANSWERING'
  | 'ANSWER_RESULT'
  | 'ROUND_END'
  | 'FINAL_BID'
  | 'FINAL_QUESTION'
  | 'FINAL_JUDGING'
  | 'GAME_OVER';

export interface Atom {
  type: string;
  content: string;
  time: number;
}

export interface CurrentQuestion {
  theme_name: string;
  price: number;
  q_type: string;
  scenario: Atom[];
  right: string[];
}

export interface BoardQuestion {
  price: number;
  played: boolean;
}

export interface BoardTheme {
  name: string;
  questions: BoardQuestion[];
}

export interface FinalJudgmentQuestion {
  scenario: Atom[];
  right: string[];
}

export interface GameState {
  phase: Phase;
  host_id: string;
  paused: boolean;
  appeal_by: string | null;
  last_judged_id: string | null;
  player_names: Record<string, string>;
  active_player_id: string | null;
  scores: Record<string, number>;
  round_name: string | null;
  board: BoardTheme[];
  current_question: CurrentQuestion | null;
  current_answerer_id: string | null;
  connected_players: string[];
  // Final round fields (present only during FINAL_* phases)
  final_round_name?: string;
  final_theme_name?: string;
  final_bids_submitted?: string[];
  final_answers_submitted?: string[];
  final_question?: FinalJudgmentQuestion;
  final_current_judge_id?: string | null;
  final_current_answer?: string;
  final_current_bid?: number;
}
