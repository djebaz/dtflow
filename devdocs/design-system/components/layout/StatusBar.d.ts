import * as React from "react";

export interface StatusBarProps {
  children?: React.ReactNode;
  /** Right-aligned slot; the Connection status button sits here at the extreme right. */
  trailing?: React.ReactNode;
}
export declare function StatusBar(props: StatusBarProps): JSX.Element;
