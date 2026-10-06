import * as React from "react";

export interface MenuBarProps {
  menus: { label: string; items: string[] }[];
}
export declare function MenuBar(props: MenuBarProps): JSX.Element;
