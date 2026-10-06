import * as React from "react";

export interface PanelProps {
  /** Optional eyebrow caption above the content. */
  eyebrow?: string;
  children?: React.ReactNode;
  style?: React.CSSProperties;
  className?: string;
}
export declare function Panel(props: PanelProps): JSX.Element;
