/**
 * View navigation context — lets any view switch the active view without
 * prop-drilling through the registry. Used by "quick action" buttons on
 * Today, Next Rep, etc. that jump to Manual Entry or Agent Chat.
 *
 * RV5-W10: added optional params support so BlogsView can pass a blog ID
 * to the Blog Editor. Params are stored in a separate context so the
 * receiving view can read them without prop-drilling.
 *
 * No useEffect — state is derived from the App's useState.
 */
import { createContext, useContext } from "react";

export interface NavParams {
  [key: string]: unknown;
}

export type NavFn = (viewId: string, params?: NavParams) => void;

export const NavContext = createContext<NavFn>(() => {});
export const NavParamsContext = createContext<NavParams>({});

/** Navigate to another view by ID, optionally passing params. */
export const useNav = () => useContext(NavContext);

/** Read params passed by the navigating view. */
export const useNavParams = () => useContext(NavParamsContext);
