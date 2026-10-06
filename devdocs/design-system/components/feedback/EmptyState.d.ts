import * as React from "react";

export interface EmptyStateProps {
  title?: string;
  children?: React.ReactNode;
  /** Optional logo URL shown above the text. */
  logo?: string;
  style?: React.CSSProperties;
}
export declare function EmptyState(props: EmptyStateProps): JSX.Element;
