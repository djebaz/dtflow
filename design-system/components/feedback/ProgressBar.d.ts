import * as React from "react";

export interface ProgressBarProps {
  value?: number;
  max?: number;
  /** Centered text inside a taller bar (e.g. "Idle", "Saved"). */
  label?: string;
  style?: React.CSSProperties;
  [key: string]: any;
}
export declare function ProgressBar(props: ProgressBarProps): JSX.Element;
