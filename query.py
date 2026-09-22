from locallib.query.Query import Query
from locallib.picarrodb import *

def setup_query(query_func):
    def wrapper(*args, **kwargs):
        query = query_func(*args, **kwargs)
        return Query(query = query)
    return wrapper

@setup_query
def get_reports_veho(customer_name, table_name = None):
    if table_name is not None:
        into_clause = f"INTO {table_name}"
    else:
        into_clause = ""

    query = f"""
    SELECT 
        C.Name AS CustomerName,
        CASE
            WHEN ReportType.Description = 'Compliance' THEN CONCAT('CR-', SUBSTRING(CONVERT(nvarchar(50), R.Id), 1, 6))
            WHEN ReportType.Description = 'Emissions' THEN CONCAT('ER-', SUBSTRING(CONVERT(nvarchar(50), R.Id), 1, 6))
            ELSE CONCAT('CR-', SUBSTRING(CONVERT(nvarchar(50), R.Id), 1, 6))
        END AS ReportName,
        R.Id AS ReportId,
        R.ReportTitle AS ReportTitle,
        R.DateStarted AS ReportDate,
        RA.ExternalId AS BoundaryName,
        RA.BoundaryType AS BoundaryType,
        RA.Shape.STAsText() AS ReportArea,
        RAC.AssetLengthKM AS ReportAssetLengthKm,
        RC.PercentCoverageAssets AS ReportPercentCoverageAssets,
        RAC.AssetLengthKM * RC.PercentCoverageAssets AS AssetCoveredLengthKm,
        STUFF((SELECT DISTINCT ', ' + L.Title
               FROM ReportLabel RL
               INNER JOIN Label L ON RL.LabelId = L.Id
               WHERE RL.ReportId = R.Id AND RL.IsActive = 1 AND L.Title IS NOT NULL
               FOR XML PATH(''), TYPE).value('.', 'NVARCHAR(MAX)'), 1, 2, '') AS Labels,
        RAC.DistributionPipeKm,
        RAC.DistributionPipeCoveredKm,
        RAC.DistributionPipePercentCovered,
        RAC.ServicePipeKm,
        RAC.ServicePipeCoveredKm,
        RAC.AreaKM2,
        RAC.AreaCoveredKM2,
        YEAR(R.DateStarted) AS ReportYear,
        MONTH(R.DateStarted) AS ReportMonth,
        DATEPART(WEEK, R.DateStarted) AS ReportWeek,
        DATEPART(QUARTER, R.DateStarted) AS ReportQuarter
    {into_clause}
    FROM
        Report R
    LEFT JOIN Customer C ON
        R.CustomerId = C.Id
    LEFT JOIN ReportLabel RL ON
        R.Id = RL.ReportId
    LEFT JOIN Label L ON
        RL.LabelId = L.Id
    LEFT JOIN ReportType ON
        R.ReportTypeId = ReportType.Id
    LEFT JOIN ReportArea RA ON R.Id = RA.ReportId
    LEFT JOIN ReportCompliance RC ON R.Id = RC.ReportId
    LEFT JOIN ReportAreaCovered RAC ON R.Id = RAC.ReportId
    WHERE
        LOWER(C.Name) = LOWER('{customer_name}')
        AND L.Title = 'Veho'
        AND RL.IsActive = 1

    GROUP BY
        C.Name,
        R.Id,
        R.ReportTitle,
        R.DateStarted,
        RA.ExternalId,
        RA.BoundaryType,
        RAC.AssetLengthKM,
        RA.Shape.STAsText(),
        ReportType.Description,
        RAC.AssetLengthKM,
        RAC.AreaCoveredKM2,
        RC.PercentCoverageAssets,
        RAC.DistributionPipeKm,
        RAC.DistributionPipeCoveredKm,
        RAC.DistributionPipePercentCovered,
        RAC.ServicePipeKm,
        RAC.ServicePipeCoveredKm,
        RAC.AreaKM2,
        RAC.AreaCoveredKM2
    """
    return query