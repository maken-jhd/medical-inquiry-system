// Neo4j initialization script for the online consultation search graph.
// This script is designed for Neo4j 5.x.
// The active ontology is intentionally runtime-oriented:
// Disease + consultation evidence labels.

// ---------------------------------------------------------------------------
// Node uniqueness constraints
// ---------------------------------------------------------------------------

CREATE CONSTRAINT disease_id IF NOT EXISTS
FOR (n:Disease)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT clinical_finding_id IF NOT EXISTS
FOR (n:ClinicalFinding)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT clinical_attribute_id IF NOT EXISTS
FOR (n:ClinicalAttribute)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT lab_test_id IF NOT EXISTS
FOR (n:LabTest)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT lab_finding_id IF NOT EXISTS
FOR (n:LabFinding)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT imaging_finding_id IF NOT EXISTS
FOR (n:ImagingFinding)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT pathogen_id IF NOT EXISTS
FOR (n:Pathogen)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT risk_factor_id IF NOT EXISTS
FOR (n:RiskFactor)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT population_group_id IF NOT EXISTS
FOR (n:PopulationGroup)
REQUIRE n.id IS UNIQUE;

// ---------------------------------------------------------------------------
// Name and canonical-name indexes for retrieval / entity linking
// ---------------------------------------------------------------------------

CREATE INDEX disease_name IF NOT EXISTS
FOR (n:Disease)
ON (n.name);

CREATE INDEX disease_canonical_name IF NOT EXISTS
FOR (n:Disease)
ON (n.canonical_name);

CREATE INDEX disease_group IF NOT EXISTS
FOR (n:Disease)
ON (n.disease_group);

CREATE INDEX clinical_finding_name IF NOT EXISTS
FOR (n:ClinicalFinding)
ON (n.name);

CREATE INDEX clinical_finding_canonical_name IF NOT EXISTS
FOR (n:ClinicalFinding)
ON (n.canonical_name);

CREATE INDEX clinical_attribute_name IF NOT EXISTS
FOR (n:ClinicalAttribute)
ON (n.name);

CREATE INDEX clinical_attribute_canonical_name IF NOT EXISTS
FOR (n:ClinicalAttribute)
ON (n.canonical_name);

CREATE INDEX lab_test_name IF NOT EXISTS
FOR (n:LabTest)
ON (n.name);

CREATE INDEX lab_test_canonical_name IF NOT EXISTS
FOR (n:LabTest)
ON (n.canonical_name);

CREATE INDEX lab_finding_name IF NOT EXISTS
FOR (n:LabFinding)
ON (n.name);

CREATE INDEX lab_finding_canonical_name IF NOT EXISTS
FOR (n:LabFinding)
ON (n.canonical_name);

CREATE INDEX imaging_finding_name IF NOT EXISTS
FOR (n:ImagingFinding)
ON (n.name);

CREATE INDEX imaging_finding_canonical_name IF NOT EXISTS
FOR (n:ImagingFinding)
ON (n.canonical_name);

CREATE INDEX pathogen_name IF NOT EXISTS
FOR (n:Pathogen)
ON (n.name);

CREATE INDEX pathogen_canonical_name IF NOT EXISTS
FOR (n:Pathogen)
ON (n.canonical_name);

CREATE INDEX risk_factor_name IF NOT EXISTS
FOR (n:RiskFactor)
ON (n.name);

CREATE INDEX risk_factor_canonical_name IF NOT EXISTS
FOR (n:RiskFactor)
ON (n.canonical_name);

CREATE INDEX population_group_name IF NOT EXISTS
FOR (n:PopulationGroup)
ON (n.name);

CREATE INDEX population_group_canonical_name IF NOT EXISTS
FOR (n:PopulationGroup)
ON (n.canonical_name);

// ---------------------------------------------------------------------------
// Evidence acquisition metadata indexes reserved for A3 ranking
// ---------------------------------------------------------------------------

CREATE INDEX clinical_finding_acquisition_mode IF NOT EXISTS
FOR (n:ClinicalFinding)
ON (n.acquisition_mode);

CREATE INDEX clinical_finding_evidence_cost IF NOT EXISTS
FOR (n:ClinicalFinding)
ON (n.evidence_cost);

CREATE INDEX clinical_attribute_acquisition_mode IF NOT EXISTS
FOR (n:ClinicalAttribute)
ON (n.acquisition_mode);

CREATE INDEX clinical_attribute_evidence_cost IF NOT EXISTS
FOR (n:ClinicalAttribute)
ON (n.evidence_cost);

CREATE INDEX lab_test_acquisition_mode IF NOT EXISTS
FOR (n:LabTest)
ON (n.acquisition_mode);

CREATE INDEX lab_test_evidence_cost IF NOT EXISTS
FOR (n:LabTest)
ON (n.evidence_cost);

CREATE INDEX lab_finding_acquisition_mode IF NOT EXISTS
FOR (n:LabFinding)
ON (n.acquisition_mode);

CREATE INDEX lab_finding_evidence_cost IF NOT EXISTS
FOR (n:LabFinding)
ON (n.evidence_cost);

CREATE INDEX imaging_finding_acquisition_mode IF NOT EXISTS
FOR (n:ImagingFinding)
ON (n.acquisition_mode);

CREATE INDEX imaging_finding_evidence_cost IF NOT EXISTS
FOR (n:ImagingFinding)
ON (n.evidence_cost);

CREATE INDEX pathogen_acquisition_mode IF NOT EXISTS
FOR (n:Pathogen)
ON (n.acquisition_mode);

CREATE INDEX pathogen_evidence_cost IF NOT EXISTS
FOR (n:Pathogen)
ON (n.evidence_cost);

CREATE INDEX risk_factor_acquisition_mode IF NOT EXISTS
FOR (n:RiskFactor)
ON (n.acquisition_mode);

CREATE INDEX risk_factor_evidence_cost IF NOT EXISTS
FOR (n:RiskFactor)
ON (n.evidence_cost);

CREATE INDEX population_group_acquisition_mode IF NOT EXISTS
FOR (n:PopulationGroup)
ON (n.acquisition_mode);

CREATE INDEX population_group_evidence_cost IF NOT EXISTS
FOR (n:PopulationGroup)
ON (n.evidence_cost);

CREATE INDEX lab_finding_semantic IF NOT EXISTS
FOR (n:LabFinding)
ON (n.test_id, n.operator, n.value, n.unit);
