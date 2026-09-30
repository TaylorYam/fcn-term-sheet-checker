# Architecture Overview

Keep this document aligned with the current system. Describe the important boundaries and reasons behind them; link to code for details that are easy to inspect there.

## Purpose and scope

Describe the problem this project solves, its users, and what is outside its scope.

## System context

List the main external systems, users, and data entering or leaving the project.

## Components and boundaries

Describe the major components, their responsibilities, and how they communicate. Add a diagram when it makes the boundaries easier to understand.

## Data and state

Describe important data entities, persistence, ownership, retention, and any runtime data that must remain local or generated.

## Runtime and deployment

Record the supported environments, deployment shape, operational dependencies, and how configuration is supplied. Keep credentials out of this document.

## Quality attributes and constraints

List the requirements that shape design choices, such as security, availability, performance, privacy, compatibility, and cost.

## Important decisions

Link to accepted architecture decision records under `docs/adr/`.

## Updating this document

Update this overview when a change alters system boundaries, data flow, deployment, or an important constraint. Record durable choices in an ADR and link them here.
