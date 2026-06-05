{
  description = "claude-grep (ccfind) — search your Claude Code conversation history by content, then resume any session";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in {
      packages = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in rec {
          claude-grep = pkgs.stdenv.mkDerivation {
            pname = "claude-grep";
            version = "0.1.0";
            src = ./.;
            nativeBuildInputs = [ pkgs.makeWrapper pkgs.python3 ];
            dontConfigure = true;
            dontBuild = true;
            installPhase = ''
              runHook preInstall
              mkdir -p $out/bin
              install -m755 ccfind $out/bin/ccfind
              patchShebangs $out/bin/ccfind
              wrapProgram $out/bin/ccfind \
                --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.fzf pkgs.ripgrep pkgs.python3 ]}
              runHook postInstall
            '';
            meta = with pkgs.lib; {
              description = "Search your Claude Code conversation history by content, then resume any session";
              homepage = "https://github.com/coolcorexix/claude-grep";
              license = licenses.mit;
              mainProgram = "ccfind";
              platforms = systems;
            };
          };
          default = claude-grep;
        });

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/ccfind";
        };
      });
    };
}
