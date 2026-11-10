<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Security policy

Tri-Queue is a research/edge matrix coprocessor, not a security boundary. It has no privilege model,
no memory protection and no isolation: the host program controls the memory ports. Do not use it to
enforce security properties without your own wrappers and review.

## Reporting a vulnerability

Please **do not** open a public issue for a security-sensitive report. Use GitHub's private
[vulnerability reporting](https://github.com/codetiger/triqueue/security/advisories/new) for this
repository, or contact the maintainer at `nharishankar@gmail.com` with:

- the affected version/commit and configuration;
- a minimal reproduction (program, memory image, or model call);
- the impact you believe it has;
- any suggested mitigation.

We will acknowledge the report, investigate, and coordinate a fix and disclosure. There is no bug
bounty.

## Scope

In scope: RTL/formal/model/driver bugs that produce wrong results, hang the core, violate the memory
contract, or allow a host program to escape the documented interface. Out of scope: physical
side-channel resistance, fault injection, silicon validation, and the security of third-party SoC
integration.
