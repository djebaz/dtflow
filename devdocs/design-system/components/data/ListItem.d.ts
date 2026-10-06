import * as React from "react";

export interface ListItemProps {
  selected?: boolean;
  children?: React.ReactNode;
  onClick?: React.MouseEventHandler<HTMLDivElement>;
  [key: string]: any;
}
export declare function ListItem(props: ListItemProps): JSX.Element;
