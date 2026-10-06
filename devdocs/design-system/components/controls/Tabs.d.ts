import * as React from "react";

export interface TabsProps {
  tabs: string[];
  value: string;
  onChange?: (tab: string) => void;
  children?: React.ReactNode;
}
export declare function Tabs(props: TabsProps): JSX.Element;
