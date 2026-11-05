import * as React from "react";

export interface NavigationProps {
  current: string;
  onChange?: (tab: string) => void;
  items?: string[];
}
export declare function Navigation(props: NavigationProps): JSX.Element;
