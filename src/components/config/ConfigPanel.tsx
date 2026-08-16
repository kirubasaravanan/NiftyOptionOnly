"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useState } from "react";
import { Save, RotateCcw, Loader2, FileText, CheckCircle2, AlertCircle, Lock } from "lucide-react";
import { toast } from "sonner";

const CONFIG_ORDER = ["risk", "strategies", "costs", "trading_hours", "broker"];

export function ConfigPanel() {
  const qc = useQueryClient();
  const { data: configs, isLoading } = useQuery({
    queryKey: ["configs"],
    queryFn: api.configs,
  });

  const [activeName, setActiveName] = useState<string>("");
  const [draft, setDraft] = useState<string>("");
  const [touched, setTouched] = useState(false);
  const [apiToken, setApiToken] = useState<string>("");
  // Persist token in localStorage so the user doesn't re-enter every session
  useState(() => {
    const stored = typeof window !== "undefined" ? localStorage.getItem("engine_api_token") : "";
    if (stored) setApiToken(stored);
  });

  // Initialise the active config name once when configs first load
  if (configs && !activeName) {
    setActiveName(CONFIG_ORDER[0]);
    setDraft(configs[CONFIG_ORDER[0]] ?? "");
  }

  const savedDraft = configs && activeName ? (configs[activeName] ?? "") : "";
  const effectiveDraft = touched ? draft : savedDraft;

  const saveMutation = useMutation({
    mutationFn: () => {
      // Store token in localStorage on save
      if (apiToken && typeof window !== "undefined") {
        localStorage.setItem("engine_api_token", apiToken);
      }
      return api.updateConfig(activeName, draft, apiToken || undefined);
    },
    onSuccess: () => {
      toast.success(`Saved ${activeName}.yaml — engine will use new thresholds on next cycle.`);
      qc.invalidateQueries({ queryKey: ["configs"] });
      setTouched(false);
    },
    onError: (err) => {
      toast.error(`Save failed: ${(err as Error)?.message ?? "unknown error"}`);
    },
  });

  const handleSelect = (name: string) => {
    setActiveName(name);
    setTouched(false);
    setDraft(configs?.[name] ?? "");
  };

  const handleDraftChange = (v: string) => {
    setDraft(v);
    setTouched(true);
  };

  const handleRevert = () => {
    setDraft(savedDraft);
    setTouched(false);
    toast.info("Reverted draft to last saved value.");
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <FileText className="h-4 w-4" />
            Engine Configuration
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground mb-3">
            All thresholds are externalised to YAML. Editing here updates the live config files
            in <code className="bg-muted px-1 rounded">nifty_engine/config/</code>. The next
            decision cycle will use the new values — no restart required.
          </p>

          <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
            {/* File list */}
            <div className="space-y-1">
              {CONFIG_ORDER.map((name) => (
                <button
                  key={name}
                  onClick={() => handleSelect(name)}
                  className={`w-full text-left px-3 py-2 rounded-md text-xs font-medium flex items-center justify-between transition-colors ${
                    activeName === name
                      ? "bg-primary text-primary-foreground"
                      : "hover:bg-muted"
                  }`}
                >
                  <span>{name}.yaml</span>
                  {configs && configs[name] && (
                    <Badge variant="outline" className="text-[10px]">
                      {configs[name].split("\n").length} lines
                    </Badge>
                  )}
                </button>
              ))}
            </div>

            {/* Editor */}
            <div className="lg:col-span-3">
              {/* API token input — required for saves since rev 5 */}
              <div className="mb-3 flex items-center gap-2">
                <Lock className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                <Input
                  type="password"
                  placeholder="ENGINE_API_TOKEN (required to save)"
                  value={apiToken}
                  onChange={(e) => setApiToken(e.target.value)}
                  className="h-7 text-xs max-w-xs"
                />
                <span className="text-[10px] text-muted-foreground">
                  {!apiToken && "⚠ Saves will 403 without a token"}
                  {apiToken && "✓ Token set (stored in browser)"}
                </span>
              </div>

              <div className="flex items-center justify-between mb-2">
                <div className="text-xs text-muted-foreground">
                  Editing: <code className="bg-muted px-1 rounded">{activeName}.yaml</code>
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleRevert}
                  >
                    <RotateCcw className="h-3 w-3 mr-1" />
                    Revert
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => saveMutation.mutate()}
                    disabled={saveMutation.isPending || !draft}
                  >
                    {saveMutation.isPending ? (
                      <>
                        <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                        Saving…
                      </>
                    ) : (
                      <>
                        <Save className="h-3 w-3 mr-1" />
                        Save
                      </>
                    )}
                  </Button>
                </div>
              </div>

              {isLoading ? (
                <div className="h-96 flex items-center justify-center text-sm text-muted-foreground">
                  Loading configs…
                </div>
              ) : (
                <Textarea
                  value={effectiveDraft}
                  onChange={(e) => handleDraftChange(e.target.value)}
                  className="font-mono text-xs h-96 resize-y"
                  spellCheck={false}
                  placeholder="# YAML configuration"
                />
              )}

              <div className="mt-2 text-[10px] text-muted-foreground">
                <CheckCircle2 className="inline h-3 w-3 text-emerald-500 mr-1" />
                Validated as YAML before save ·
                <AlertCircle className="inline h-3 w-3 text-amber-500 ml-2 mr-1" />
                Changes apply to the next decision cycle
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
